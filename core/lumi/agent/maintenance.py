"""Housekeeping that runs because Lumi started, not because anything asked.

Design → docs/architecture/memory.md §5 / docs/contracts/privacy.md §4

Three jobs, all `Job(actor=system)` — background work that never takes foreground and
never touches an L1 tool (ADR-018). None of them needs inference, so none takes a lease;
there is nothing here for barge-in to compete with.

**Once per start rather than on a timer.** Decay and retention are both functions of
elapsed time, and a timer that fires while the machine is asleep does nothing at all. The
honest description is "expired records are removed when Lumi runs", which is what
docs/contracts/privacy.md §4 promises.

**A failure here never stops startup.** Refusing to start because old records could not
be deleted would trade the whole product for a chore.
"""

from __future__ import annotations

from lumi import logging as lumi_logging
from lumi import settings as settings_module
from lumi.kernel.cancellation import Cancellation
from lumi.kernel.ids import new_job_id
from lumi.kernel.job import Job, JobKind
from lumi.memory.indexing import Indexer
from lumi.memory.store import MemoryStore
from lumi.memory.vectors import MemoryIndex
from lumi.providers.embedding.harrier import HarrierEmbeddingProvider
from lumi.settings import Settings
from lumi.storage.retention import RetentionPolicy, RetentionService

log = lumi_logging.get_logger(__name__)


def _retention_days(value: str, default: int | None) -> int | None:
    """A settings value as a number of days. **`unlimited` is `None`, not a large number.**

    `lumi.settings` already refuses anything else, so reaching the fallback means the two
    have drifted. It **keeps the deadline** rather than removing it: the failure mode of
    guessing "unlimited" is a database that grows forever because of a typo.
    """
    if value == settings_module.UNLIMITED:
        return None
    try:
        return int(value)
    except ValueError:
        log.warning("retention.invalid_setting", value=value)
        return default


class MaintenanceJobs:
    """The sweeps a start performs before, and while, anyone is talking."""

    __slots__ = ("_embedder", "_index", "_memories", "_retention", "_settings")

    def __init__(
        self,
        *,
        settings: Settings,
        retention: RetentionService,
        memories: MemoryStore,
        index: MemoryIndex,
        embedder: HarrierEmbeddingProvider | None,
    ) -> None:
        self._settings = settings
        self._retention = retention
        self._memories = memories
        self._index = index
        self._embedder = embedder

    async def expire_old_records(self) -> None:
        """Delete what is past its deadline. **Every start, before anything is written.**

        Run as a `Job`: it is background work with `actor=system`, so it never takes
        foreground and never touches an L1 tool (ADR-018). It needs no inference and
        therefore takes no lease — there is nothing here for barge-in to compete with.

        **Once per start rather than on a timer.** A machine that is on for a week keeps
        records a few days past their deadline; a timer that fires while Lumi is asleep
        does nothing at all. The honest description is "expired records are removed when
        Lumi runs", and that is what docs/contracts/privacy.md §4 promises.

        A failure here is logged and does not stop startup. **Refusing to start because
        old records could not be deleted would trade the whole product for a chore.**
        """
        job = Job(id=new_job_id(), kind=JobKind.MAINTENANCE, cancellation=Cancellation.COOPERATIVE)
        default = RetentionPolicy()
        policy = RetentionPolicy(
            episode_days=_retention_days(
                self._settings.retention_episodes.value, default.episode_days
            ),
            event_days=_retention_days(self._settings.retention_events.value, default.event_days),
            audit_days=_retention_days(self._settings.retention_audit.value, default.audit_days),
        )
        try:
            deletions = await self._retention.run(policy)
        except Exception:
            log.exception("retention.failed", job=str(job.id))
            return
        removed = sum(deletion.count for deletion in deletions)
        log.info("retention.done", job=str(job.id), removed=removed)

    async def forget_faded_memories(self) -> None:
        """Archive memories that have decayed below the floor. **Not a deletion.**

        The rows stay; they stop turning up in ordinary retrieval
        (docs/architecture/memory.md §5). Deleting them would be destruction — forgetting
        is supposed to be recoverable by a strong enough cue.

        Runs in the same `Job` shape as retention, and for the same reason as its timing:
        decay is a function of elapsed time, so a sweep at each start is what "expired
        while Lumi was off" means in practice.
        """
        job = Job(id=new_job_id(), kind=JobKind.MAINTENANCE, cancellation=Cancellation.COOPERATIVE)
        try:
            faded = await self._memories.archive_faded()
        except Exception:
            log.exception("memory.archive_failed", job=str(job.id))
            return
        log.info("memory.archive_done", job=str(job.id), archived=len(faded))

    async def index_memories(self) -> None:
        """Embed anything unindexed, and re-embed after a model change.

        A `Job` like the other two, and **not awaited by startup**: the first run against a
        long history is seconds of CPU, and what shares that CPU is capture, VAD and
        barge-in. Retrieval works throughout — an unindexed memory is reachable through
        recency and keywords, just not by similarity.
        """
        if self._embedder is None:
            return
        job = Job(id=new_job_id(), kind=JobKind.MAINTENANCE, cancellation=Cancellation.COOPERATIVE)
        try:
            await self._embedder.load()
            report = await Indexer(self._memories, self._index, self._embedder).run_until_done()
        except Exception:
            # **The conversation does not depend on this.** Memory search stays degraded
            # until the next start, and the reason is in the log rather than in a silence.
            log.exception("memory.index_failed", job=str(job.id))
            return
        log.info("memory.index_done", job=str(job.id), embedded=report.embedded)
