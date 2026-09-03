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
from enum import StrEnum
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

#: The batch this pass will not go below. **A floor is what makes the retry terminate** —
#: see `_reflect_episode`.
MIN_UTTERANCE_BATCH: Final = 1


class _Ask(StrEnum):
    """How the model's answer ended. **Not the same question as "did it parse"** — a
    truncated JSON array can still parse into a shorter list, which is the whole reason
    the reason code is read rather than the text.
    """

    OK = "ok"
    #: Revoked mid-stream, or the engine broke. **Try the same thing again later**
    FAILED = "failed"
    #: `Finish(reason="length")` — ran out of output tokens with the array half-written.
    #: **Trying the same thing again later produces the same truncation**
    TRUNCATED = "truncated"


#: `Finish.reason` → what to do about it. **Anything unrecognised is a failure**, because a
#: reason nobody has read cannot be assumed to mean the answer is whole (fail-closed).
_FINISH_OUTCOMES: Final[dict[str, _Ask]] = {"stop": _Ask.OK, "length": _Ask.TRUNCATED}


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
                episode_report, batch = await self._reflect_episode(episode_id, lines, job)
                report = _merge(report, episode_report)
                if episode_report.interrupted:
                    return report
                # **The watermark moves only after the writes landed**, and only over the
                # batch that was actually read — a shrunk batch leaves the rest for the
                # next pass. Moving it first would mean a crash costs the memories and
                # hides that it did.
                await self._episodes.mark_reflected(episode_id, batch[-1].turn_index + 1)
        log.info(
            "reflection.done",
            job=str(job.id),
            episodes=report.episodes,
            written=report.written,
            superseded=report.superseded,
            rejected=len(report.rejected),
        )
        return report

    async def _reflect_episode(
        self, episode_id: str, lines: Sequence[Utterance], job: Job
    ) -> tuple[ReflectionReport, Sequence[Utterance]]:
        """One episode, and **the batch it ended up reading.**

        A revoked lease and a token-capped answer both mean "throw the result away", but
        only the first is worth retrying unchanged. `unreflected()` returns episodes oldest
        first and `UTTERANCE_LIMIT` is fixed, so **an episode whose batch needs more than
        `max_tokens` of JSON would truncate again on every later pass** — forever, blocking
        every episode behind it and spending idle inference to stay stuck.

        So the batch halves until the answer fits. At `MIN_UTTERANCE_BATCH` it stops and the
        watermark moves anyway: **one utterance nobody can extract from is a loss worth
        recording; a reflection queue that never drains is a broken feature.** The loss is
        loud — a `rejected` reason in the report and an error in the log.
        """
        batch = lines[:UTTERANCE_LIMIT]
        while True:
            report, truncated = await self._reflect(episode_id, batch, job)
            if not truncated:
                return report, batch
            if len(batch) <= MIN_UTTERANCE_BATCH:
                log.error(
                    "reflection.truncated_at_floor",
                    episode=episode_id,
                    turn=batch[-1].turn_index,
                )
                reason = f"truncated: turn {batch[-1].turn_index} exceeded max_tokens"
                return ReflectionReport(rejected=(reason,), episodes=1), batch
            batch = batch[: len(batch) // 2]
            log.warning("reflection.batch_halved", episode=episode_id, utterances=len(batch))

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
    ) -> tuple[ReflectionReport, bool]:
        """One prompt over one batch, and **whether it was the batch that was too big.**"""
        known = [record.subject for record in await self._store.recent(20)]
        messages = build_messages(lines, known_subjects=known)
        try:
            answer, ending = await self._ask(messages, job)
        except Exception as error:
            # **An engine that is not there is not a reason to lose the transcript.** The
            # watermark stays put and the next pass tries again.
            log.warning("reflection.llm_failed", error=str(error), episode=episode_id)
            return ReflectionReport(interrupted=True, episodes=1), False
        if job.cancel_token.is_set:
            # **Revocation wins over truncation.** The conversation wants the GPU now, and
            # a smaller batch is still a second inference.
            return ReflectionReport(interrupted=True, episodes=1), False
        if ending is not _Ask.OK:
            return ReflectionReport(interrupted=True, episodes=1), ending is _Ask.TRUNCATED

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
        return (
            ReflectionReport(
                written=written,
                superseded=superseded,
                duplicates=duplicates,
                rejected=tuple(rejected),
                episodes=1,
            ),
            False,
        )

    async def _ask(self, messages: Sequence[Message], job: Job) -> tuple[str, _Ask]:
        """The model's whole answer, and **how it ended.** Streamed, because that is the
        only interface — the pieces are joined here rather than being spoken.
        """
        chunks: list[str] = []
        # A stream that ends without a `Finish` did not finish.
        ending = _Ask.FAILED
        async for event in self._llm.stream(messages, None, self._options, job.cancel_token):
            if job.cancel_token.is_set:
                # **Cooperative.** The lease was revoked; the conversation wants the GPU.
                return "", _Ask.FAILED
            if isinstance(event, TextDelta):
                chunks.append(event.text)
            elif isinstance(event, LLMFailure):
                log.warning("reflection.stream_failed", detail=event.message)
                return "".join(chunks), _Ask.FAILED
            elif isinstance(event, Finish):
                if event.reason != "stop":
                    log.warning("reflection.stream_incomplete", reason=event.reason)
                ending = _FINISH_OUTCOMES.get(event.reason, _Ask.FAILED)
                break
        return "".join(chunks), ending


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
