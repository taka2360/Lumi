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

## "Try again next time" is the wrong answer to one ending

A revoked lease and an engine that fell over are both worth retrying **unchanged**: the
request was fine and something outside it was not. `Finish(reason="length")` is the
opposite — the request is what did not fit — and extraction reads the same episodes again
next pass, so retrying it unchanged truncates in the same place forever, taking every
episode behind it down with it (`unreflected()` is oldest-first). **So the batch shrinks
inside the pass.** The ending-by-ending table is docs/architecture/memory.md §4 (ADR-049);
`_Ask` is where it is implemented.

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

#: Extra generations allowed once the batch is already at the floor and still comes back
#: `length`. **Above zero and bounded, for opposite reasons** (ADR-049).
#:
#: Above zero because extraction runs at `temperature 0.2` with **no seed** — production
#: never sets one (`LLMOptions.seed`). One `length` at the floor is therefore a sample,
#: not a property of the utterance: a single runaway generation is not evidence that this
#: sentence can never be extracted from, and treating it as evidence throws the sentence
#: away forever on a coin flip.
#:
#: Bounded because the floor is where shrinking has run out of room. Past this, repeating
#: is idle inference spent to stay stuck, and every episode behind this one stays stuck
#: with it (`unreflected()` is oldest-first).
FLOOR_TRUNCATION_RETRIES: Final = 2


class _Ask(StrEnum):
    """How the model's answer ended, and **what the pass may do about it.**

    Not the same question as "did it parse" — a truncated JSON array can still parse into
    a shorter list, which is the whole reason the reason code is read rather than the text.

    **The ending-by-ending table lives in docs/architecture/memory.md §4** (what is
    retried, what the watermark does, what ends up in the report). What is written here is
    why there are three members and not one per `Finish.reason`.

    `UNUSABLE` is one member on purpose. What its cases share is that **the request was
    fine and something outside it was not**, so the same request is worth making again
    unchanged, later. `OVERSIZE` is the opposite: the request is what did not fit, and
    repeating it unchanged is exactly how the queue stopped draining.

    ★ **An unrecognised `Finish.reason` is `UNUSABLE`, and that is a choice to block
    rather than to forget.** A reason nobody has read cannot be assumed to mean the answer
    is whole, and it cannot be told apart from an engine that gave up server-side — so the
    transcript is kept and the queue stops. That stall is visible (`unreflected_turns()` is
    what the memory window shows) and logged at `error`; a watermark moved on a reason
    nobody understood would be silent and permanent.
    """

    OK = "ok"
    #: Revoked mid-stream, the engine broke, or the answer ended in a way this cannot read.
    #: **Try the same thing again later, unchanged**
    UNUSABLE = "unusable"
    #: `Finish(reason="length")` — ran out of output tokens with the array half-written.
    #: **Asking the same batch again is asking for the same truncation**
    OVERSIZE = "oversize"


#: `Finish.reason` → what to do about it. **Anything unrecognised is `UNUSABLE`** — see the
#: `_Ask` docstring (fail-closed: keep the transcript, stop the queue, say so).
_FINISH_OUTCOMES: Final[dict[str, _Ask]] = {"stop": _Ask.OK, "length": _Ask.OVERSIZE}


@dataclass(frozen=True, slots=True)
class ReflectionReport:
    """What a pass did. **Every number is a fact about this run**, not a running total."""

    written: int = 0
    superseded: int = 0
    duplicates: int = 0
    rejected: tuple[str, ...] = ()
    #: The pass stopped early and **nothing it was in the middle of was kept.** The
    #: watermark did not move for the episode it stopped on, so the same utterances are
    #: read again next time (`_Ask.UNUSABLE`).
    #:
    #: **Not the same as "the model did not finish naturally"**: an answer given up on at
    #: the floor did not finish naturally either, and that one moves the watermark and
    #: leaves a `rejected` reason instead.
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
        """One pass over the unreflected conversation. **Safe to call when there is none.**

        ★ **Whatever this wrote, it reports — including when it dies partway.** The report
        is the only record of what landed: the scheduler reads `learned` to decide whether
        to embed the new memories and whether to nudge the memory window (ADR-042). Letting
        an exception replace the report would throw that away for writes that are already
        committed, and the pass that retries them afterwards sees its own writes as
        duplicates — `learned` is 0, so **the memories stay unembedded and unfindable by
        similarity until the next restart.**
        """
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
                try:
                    lines = await self._episodes.utterances_from(episode_id, watermark)
                    if not lines:
                        continue
                    episode_report, batch = await self._reflect_episode(episode_id, lines, job)
                    report = _merge(report, episode_report)
                    if episode_report.interrupted:
                        return report
                    # **The watermark moves only after the writes landed**, and only over
                    # the batch that was actually read — a shrunk batch leaves the rest for
                    # the next pass. Moving it first would mean a crash costs the memories
                    # and hides that it did.
                    await self._episodes.mark_reflected(episode_id, batch[-1].turn_index + 1)
                    if len(batch) < len(lines):
                        # ★ **An episode that is not finished blocks the newer ones behind
                        # it**, because reading order *is* belief precedence. `unreflected()`
                        # returns episodes oldest-first (`ORDER BY started_at`) and
                        # `contradiction.resolve()` lets equal strength go to whichever
                        # candidate arrives later — together those two make "Tuesday
                        # supersedes Monday" true. Carrying on to a newer episode here
                        # would write Tuesday's belief now and Monday's remainder next
                        # pass, so **the older one would supersede the newer**: a fact
                        # quietly deleting a fresher fact (memory.md §3).
                        #
                        # Reached by a halved batch and by an episode longer than
                        # `UTTERANCE_LIMIT` alike. Ending the pass costs one idle period,
                        # not a memory: nothing is thrown away, the watermark keeps what it
                        # earned, and the next pass finds this same episode first.
                        log.info(
                            "reflection.episode_unfinished",
                            episode=episode_id,
                            read=len(batch),
                            pending=len(lines) - len(batch),
                        )
                        break
                except Exception:
                    # ★ **The counts survive; the pass does not.** A locked database on
                    # `mark_reflected()` is the sharp case: the memories are committed, the
                    # watermark is not, and losing the report here would mean nobody embeds
                    # them — the retry that follows classifies its own writes as duplicates
                    # (see `run`'s docstring). Reporting `interrupted` instead is already
                    # what a revoked lease does, and it is true of this too.
                    log.exception("reflection.episode_failed", episode=episode_id)
                    return _interrupted(report)
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

        **`report.interrupted` is the whole answer to "may the watermark move"**: the
        caller moves it exactly when this returns false, and nothing else in here gets a
        say. Every path below has to leave that flag true for a result that was thrown
        away and false for one that was acted on — including "acted on" meaning "given up
        on, loudly".

        A revoked lease and a token-capped answer both mean "throw the result away", but
        only the first is worth retrying unchanged. `unreflected()` returns episodes oldest
        first and `UTTERANCE_LIMIT` is fixed, so **an episode whose batch needs more than
        `max_tokens` of JSON would truncate again on every later pass** — forever, blocking
        every episode behind it and spending idle inference to stay stuck.

        So the batch halves until the answer fits. At `MIN_UTTERANCE_BATCH` halving has no
        room left, and **the same batch is asked `FLOOR_TRUNCATION_RETRIES` more times
        before it is given up on** — extraction is sampled, not computed, so one `length`
        on one utterance is a run of bad luck until it has repeated. When it has, the
        watermark moves anyway: **one utterance nobody can extract from is a loss worth
        recording; a reflection queue that never drains is a broken feature.** The loss is
        loud — a `rejected` reason in the report and an error in the log.

        Every attempt, halved or repeated, re-enters `_reflect` and so re-checks the
        cancel token immediately before generating (ADR-018).

        **The loop is bounded twice over**, which is what keeps a stuck episode from
        becoming a retry storm: halving from `UTTERANCE_LIMIT` gives at most six attempts,
        the floor adds `FLOOR_TRUNCATION_RETRIES` more, and `run()` moves on to the next
        episode rather than re-entering this one.
        """
        batch = lines[:UTTERANCE_LIMIT]
        floor_attempts = 0
        while True:
            report, ending = await self._reflect(episode_id, batch, job)
            if ending is not _Ask.OVERSIZE:
                # `OK` → the watermark moves over `batch`. `UNUSABLE` → the report carries
                # `interrupted` and the caller ends the pass without moving it.
                return report, batch
            if len(batch) > MIN_UTTERANCE_BATCH:
                batch = batch[: len(batch) // 2]
                log.warning("reflection.batch_halved", episode=episode_id, utterances=len(batch))
                continue
            floor_attempts += 1
            if floor_attempts <= FLOOR_TRUNCATION_RETRIES:
                # **The same batch, deliberately.** There is nothing left to shrink, and at
                # temperature 0.2 with no seed the next sample is genuinely a new one
                log.warning(
                    "reflection.floor_retry",
                    episode=episode_id,
                    turn=batch[-1].turn_index,
                    attempt=floor_attempts,
                )
                continue
            log.error(
                "reflection.truncated_at_floor",
                episode=episode_id,
                turn=batch[-1].turn_index,
                attempts=floor_attempts,
            )
            reason = f"truncated: turn {batch[-1].turn_index} exceeded max_tokens"
            return ReflectionReport(rejected=(reason,), episodes=1), batch

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
    ) -> tuple[ReflectionReport, _Ask]:
        """One prompt over one batch, and **how the answer ended.**

        The report's `interrupted` and the returned `_Ask` say two different things, and
        only together do they say enough: `interrupted` is "the pass stops here and the
        watermark stays", `_Ask` is "and this is why, so this is what may be tried next".
        """
        known = [record.subject for record in await self._store.recent(20)]
        messages = build_messages(lines, known_subjects=known)
        # Read into a local at each of the two checkpoints below. **They are two different
        # questions asked of a value that moves** — "may this generation start" and "did
        # the conversation take the GPU while it ran" — and collapsing them into one read
        # is how the second one would go missing.
        revoked_before: bool = job.cancel_token.is_set
        if revoked_before:
            # ★ **The checkpoint is here, not only after the answer.**
            # `OllamaProvider.stream()` posts `/api/chat` before it ever looks at the
            # token, so an attempt started after the lease was revoked — a halved batch or
            # a floor retry alike — would put a second background inference in front of
            # the user's turn, the one thing revocation exists to prevent (ADR-018).
            # `run()` checks once per episode, but revocation lands on whichever await the
            # Job is sitting on, and `utterances_from()` and `recent()` are both after it.
            return ReflectionReport(interrupted=True, episodes=1), _Ask.UNUSABLE
        try:
            answer, ending = await self._ask(messages, job)
        except Exception as error:
            # **An engine that is not there is not a reason to lose the transcript.** The
            # watermark stays put and the next pass tries again.
            log.warning("reflection.llm_failed", error=str(error), episode=episode_id)
            return ReflectionReport(interrupted=True, episodes=1), _Ask.UNUSABLE
        revoked_while_streaming: bool = job.cancel_token.is_set
        if revoked_while_streaming:
            # **Revocation wins over an oversize answer.** A halved batch and a floor retry
            # are both still a second background generation, and the floor's "give up and
            # move the watermark" verdict is a decision this pass no longer has the
            # standing to make. Nothing is written and the watermark stays where it was.
            return ReflectionReport(interrupted=True, episodes=1), _Ask.UNUSABLE
        if ending is not _Ask.OK:
            # `OVERSIZE` is not an interruption — `_reflect_episode` is about to try again
            # inside this same pass, and only what it does with the last attempt decides
            # whether the watermark moves.
            return ReflectionReport(interrupted=ending is _Ask.UNUSABLE, episodes=1), ending

        items, rejected = parse_extractions(answer)
        by_id = {line.id: line for line in lines}
        written = superseded = duplicates = 0
        interrupted = False
        for item in items:
            try:
                candidate = to_candidate(
                    item,
                    lines=by_id,
                    episode_id=episode_id,
                    novelty=await self._novelty(item),
                )
                outcome = await self._store.reconcile(candidate, now=self._clock())
            except ReflectionRejected as error:
                rejected.append(str(error))
                continue
            except MemoryRejected as error:
                # The store checks the same things again and knows more than this does.
                rejected.append(f"store: {error}")
                continue
            except Exception:
                # ★ **`run`'s rule, one frame down.** Two memories from one answer and the
                # store dies between them: the first is committed, so it has to be counted
                # or nobody embeds it, and the watermark must not move or the second is
                # skipped forever. **This is not the item's `rejected` reason** — `rejected`
                # means "this extraction was no good", and this one may have been fine.
                log.exception("reflection.write_failed", episode=episode_id)
                interrupted = True
                break
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
                interrupted=interrupted,
                episodes=1,
            ),
            # **`UNUSABLE`, not `OK`** — the answer was whole, but this pass did not finish
            # acting on it, and `OK` is what would move the watermark past the items it
            # never wrote. Shrinking the batch would not help either: nothing overflowed
            _Ask.UNUSABLE if interrupted else _Ask.OK,
        )

    async def _ask(self, messages: Sequence[Message], job: Job) -> tuple[str, _Ask]:
        """The model's whole answer, and **how it ended.** Streamed, because that is the
        only interface — the pieces are joined here rather than being spoken.
        """
        chunks: list[str] = []
        # **A stream that ends without a `Finish` did not finish**, and an answer that was
        # cut off mid-array parses into a shorter one just as happily. So the loop starts
        # from "this cannot be used" and only a `Finish` it recognises moves it off that.
        ending = _Ask.UNUSABLE
        async for event in self._llm.stream(messages, None, self._options, job.cancel_token):
            if job.cancel_token.is_set:
                # **Cooperative.** The lease was revoked; the conversation wants the GPU.
                return "", _Ask.UNUSABLE
            if isinstance(event, TextDelta):
                chunks.append(event.text)
            elif isinstance(event, LLMFailure):
                log.warning("reflection.stream_failed", detail=event.message)
                return "".join(chunks), _Ask.UNUSABLE
            elif isinstance(event, Finish):
                ending = _FINISH_OUTCOMES.get(event.reason, _Ask.UNUSABLE)
                if ending is _Ask.UNUSABLE:
                    # **Loud, because this is the ending that stops the queue** rather than
                    # costing one batch. Nobody watches a warning; this is the line that
                    # explains why reflection stopped making progress (see `_Ask`).
                    log.error("reflection.unknown_finish_reason", reason=event.reason)
                elif ending is _Ask.OVERSIZE:
                    log.warning("reflection.stream_incomplete", reason=event.reason)
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
