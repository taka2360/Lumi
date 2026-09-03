"""Reflection — **memories are made afterwards, never during the conversation.**

Design → docs/architecture/memory.md §4 / Interface → docs/interfaces/memory.md /
Job semantics → ADR-018

## Why this is a Job and not part of the turn

Extracting memories is an LLM call. Running it inside a turn would put a second inference
in front of the first token; running it outside the Arbiter's knowledge would let it hold
the GPU while the user is talking — **and barge-in would be broken by a background chore**
(memory.md §4). So it takes an `inference_lease`, and when the foreground asks for
inference the lease is revoked mid-stream.

**Revoked means the work is thrown away**, not saved half-done. Reflection is not urgent;
the watermark stays where it was and the next pass reads the same utterances again. Partial
results would mean a belief formed from the first half of a sentence.

## Three things, and only one of them is here

| | |
|---|---|
| `memory/extraction_prompt.py` | **what is asked.** A value, so a test can snapshot it |
| `memory/extraction.py` | **what comes back**, checked. Trust, evidence, salience |
| this module | **when it runs and what happens to the results.** Leases, watermarks |

The prompt has broken twice on real hardware without any of the code around it changing
(memory.md §4), which is the reason the three are not one file: **the thing that fails is
the one thing that reads as prose.**

## The transcript is data, not instruction

What the extractor reads is text that arrived from outside Lumi — including, one day, a web
page someone read aloud. It goes in an isolation block with the same rule as everywhere
else (Invariant 3), and **anything that looks like an instruction inside it is content**.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from lumi import logging as lumi_logging
from lumi.kernel.arbiter import AttentionArbiter
from lumi.kernel.cancellation import Cancellation
from lumi.kernel.ids import new_job_id
from lumi.kernel.job import Job, JobKind
from lumi.memory.contradiction import Resolution
from lumi.memory.extraction import ReflectionRejected, parse_extractions, to_candidate
from lumi.memory.extraction_prompt import build_messages
from lumi.memory.store import MemoryRejected, MemoryStore
from lumi.providers.llm.base import Finish, LLMFailure, LLMOptions, LLMProvider, Message, TextDelta
from lumi.storage.memory import EpisodeStore, Utterance

log = lumi_logging.get_logger(__name__)

#: How many episodes one pass will look at.
EPISODE_LIMIT: Final = 4

#: How many utterances of one episode go into a single prompt. **Bounded**: a long session
#: would otherwise build a prompt larger than the model's context and fail as a whole.
UTTERANCE_LIMIT: Final = 40


@dataclass(frozen=True, slots=True)
class ReflectionReport:
    """What a pass did. **Every number is a fact about this run**, not a running total."""

    written: int = 0
    superseded: int = 0
    duplicates: int = 0
    rejected: tuple[str, ...] = ()
    #: The lease was revoked, or the model did not finish naturally. **The watermark did
    #: not move.**
    interrupted: bool = False
    episodes: int = 0

    @property
    def learned(self) -> int:
        return self.written + self.superseded


class ReflectionJob:
    """Extracts memories from what has been said since the last pass.

    **`Job(kind=reflection, actor=system, uses_inference=True)`** — it never takes
    foreground, never touches an L1 tool, and yields inference the moment the conversation
    wants it (ADR-018).
    """

    __slots__ = ("_arbiter", "_clock", "_episodes", "_llm", "_options", "_store")

    def __init__(
        self,
        *,
        arbiter: AttentionArbiter,
        llm: LLMProvider,
        store: MemoryStore,
        episodes: EpisodeStore,
        options: LLMOptions,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._arbiter = arbiter
        self._llm = llm
        self._store = store
        self._episodes = episodes
        self._options = options
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> ReflectionReport:
        """One pass over the unreflected conversation. **Safe to call when there is none.**"""
        pending = await self._episodes.unreflected(EPISODE_LIMIT)
        if not pending:
            return ReflectionReport()

        job = Job(
            id=new_job_id(),
            kind=JobKind.REFLECTION,
            cancellation=Cancellation.COOPERATIVE,
            uses_inference=True,
        )
        report = ReflectionReport()
        async with self._arbiter.inference_lease(job) as lease:
            for episode_id, watermark in pending:
                if lease.token.is_set:
                    # **Stop where the revocation found us.** What has been written stays
                    # written; what has not is read again next time.
                    return _interrupted(report)
                lines = await self._episodes.utterances_from(episode_id, watermark)
                if not lines:
                    continue
                episode_report = await self._reflect(episode_id, lines[:UTTERANCE_LIMIT], job)
                report = _merge(report, episode_report)
                if episode_report.interrupted:
                    return report
                # **The watermark moves only after the writes landed.** Moving it first
                # would mean a crash costs the memories and hides that it did.
                await self._episodes.mark_reflected(
                    episode_id, lines[:UTTERANCE_LIMIT][-1].turn_index + 1
                )
        log.info(
            "reflection.done",
            job=str(job.id),
            episodes=report.episodes,
            written=report.written,
            superseded=report.superseded,
            rejected=len(report.rejected),
        )
        return report

    async def _novelty(self, item: Mapping[str, Any]) -> float:
        """How far this sits from what is already believed. **1.0 means nothing like it.**

        Measured at the subject, which is the store's own notion of "about the same thing"
        — the extraction prompt is written so that one subject is one topic. It is coarse:
        a second, unrelated fact about a subject we already know reads as familiar. The
        finer answer is embedding distance, and it cannot be had here, because a candidate
        is embedded by the `Indexer` **after** it has been written.
        """
        subject = str(item.get("subject", "")).strip()
        if not subject:
            return 0.0  # `to_candidate` is about to reject it anyway
        return 0.0 if await self._store.live(subject) else 1.0

    async def _reflect(
        self, episode_id: str, lines: Sequence[Utterance], job: Job
    ) -> ReflectionReport:
        known = [record.subject for record in await self._store.recent(20)]
        messages = build_messages(lines, known_subjects=known)
        try:
            answer, failed = await self._ask(messages, job)
        except Exception as error:
            # **An engine that is not there is not a reason to lose the transcript.** The
            # watermark stays put and the next pass tries again.
            log.warning("reflection.llm_failed", error=str(error), episode=episode_id)
            return ReflectionReport(interrupted=True, episodes=1)
        if failed or job.cancel_token.is_set:
            return ReflectionReport(interrupted=True, episodes=1)

        items, rejected = parse_extractions(answer)
        by_id = {line.id: line for line in lines}
        written = superseded = duplicates = 0
        for item in items:
            try:
                candidate = to_candidate(
                    item,
                    lines=by_id,
                    episode_id=episode_id,
                    novelty=await self._novelty(item),
                )
            except ReflectionRejected as error:
                rejected.append(str(error))
                continue
            try:
                outcome = await self._store.reconcile(candidate, now=self._clock())
            except MemoryRejected as error:
                # The store checks the same things again and knows more than this does.
                rejected.append(f"store: {error}")
                continue
            if outcome.resolution is Resolution.DUPLICATE:
                duplicates += 1
            elif outcome.resolution is Resolution.SUPERSEDE:
                superseded += 1
            else:
                written += 1
        if rejected:
            log.info("reflection.rejected", episode=episode_id, reasons=rejected)
        return ReflectionReport(
            written=written,
            superseded=superseded,
            duplicates=duplicates,
            rejected=tuple(rejected),
            episodes=1,
        )

    async def _ask(self, messages: Sequence[Message], job: Job) -> tuple[str, bool]:
        """The model's whole answer. **Streamed, because that is the only interface** — the
        pieces are joined here rather than being spoken.
        """
        chunks: list[str] = []
        failed = False
        completed = False
        async for event in self._llm.stream(messages, None, self._options, job.cancel_token):
            if job.cancel_token.is_set:
                # **Cooperative.** The lease was revoked; the conversation wants the GPU.
                return "", True
            if isinstance(event, TextDelta):
                chunks.append(event.text)
            elif isinstance(event, LLMFailure):
                log.warning("reflection.stream_failed", detail=event.message)
                failed = True
            elif isinstance(event, Finish):
                completed = event.reason == "stop"
                if not completed:
                    log.warning("reflection.stream_incomplete", reason=event.reason)
                break
        return "".join(chunks), failed or not completed


def _merge(total: ReflectionReport, one: ReflectionReport) -> ReflectionReport:
    return ReflectionReport(
        written=total.written + one.written,
        superseded=total.superseded + one.superseded,
        duplicates=total.duplicates + one.duplicates,
        rejected=total.rejected + one.rejected,
        interrupted=total.interrupted or one.interrupted,
        episodes=total.episodes + one.episodes,
    )


def _interrupted(report: ReflectionReport) -> ReflectionReport:
    log.info("reflection.revoked", written=report.written, superseded=report.superseded)
    return ReflectionReport(
        written=report.written,
        superseded=report.superseded,
        duplicates=report.duplicates,
        rejected=report.rejected,
        interrupted=True,
        episodes=report.episodes,
    )
