"""Speculative STT — **the one place STT is ever started.**

Design → docs/architecture/audio.md §7 / Decision → ADR-039

`vad_ms` (0.43 s) is spent waiting to find out whether the user is actually done talking.
**The utterance body is already complete when that wait begins**, and STT does nothing
during it. So transcription starts at `SILENCE_STARTED` instead, and the result is adopted
at `SPEECH_ENDED` — if it still describes the same audio.

## Adoption is by generation, never by "whoever finished first"

Deciding by speed makes **Lumi answer a sentence the user did not say**: a speculation
covering only the first sentence finishes before the re-run that includes the rest. So
every execution carries the buffer generation it was given, and only an exact match is
adopted. Anything else — a stale generation, a failure, no result at all — falls back to
running the confirmed buffer (fail-closed).

## Bounded by refusing to start, not by cancelling

**STT inference cannot be cancelled.** It runs in `asyncio.to_thread`, and cancelling the
awaiting side leaves the thread running to completion (`non_cancellable` in the terms of
docs/contracts/state-machines.md). Boundedness therefore comes from three rules:

| Rule | |
|---|---|
| **single-flight** | At most one execution at a time. Nothing here ever runs two |
| **latest-wins pending** | A newer generation *replaces* the one waiting. Never a queue |
| **per-turn cap** | After `max_speculations_per_turn`, the turn gives up and runs once at the end |

Reaching the cap **drops the pending slot in the same step that sets the flag**. Splitting
those two lets the worker come free in between and start a fourth run in a turn that had
already given up on speculating.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from lumi import logging as lumi_logging
from lumi.audio.ring import Samples
from lumi.providers.stt.base import Transcription

log = lumi_logging.get_logger(__name__)

#: How many speculations one turn may start before it gives up and waits for the end
#: (ADR-039). **A turn that hits this is as slow as Phase 1 was, and that is the intent** —
#: it only happens when the user has already spent seconds pausing mid-utterance.
MAX_SPECULATIONS_PER_TURN: Final = 3


@dataclass(frozen=True, slots=True)
class SttOutcome:
    """What one turn's transcription cost, and where it came from.

    Timestamps are `perf_counter` readings so the caller can work out how much of this
    overlapped the VAD wait. **The span starts when the execution was *requested*, not when
    inference began**: the gap between them is time spent waiting for an uncancellable
    predecessor, and starting the clock later would make that delay belong to nobody.
    """

    transcription: Transcription
    #: Whether the adopted result came from a speculation (vs. a run started at the end)
    speculative: bool
    requested_at: float
    started_at: float
    available_at: float
    #: Total inference time of executions that were thrown away. **Not part of `stt_ms`** —
    #: whatever of it was on the critical path is already inside `wait_ms`
    discarded_ms: int
    discarded: int
    #: Whether this turn stopped speculating because it hit the cap
    capped: bool

    @property
    def stt_ms(self) -> int:
        return _ms(self.available_at - self.requested_at)

    @property
    def wait_ms(self) -> int:
        """Of `stt_ms`, how long was spent waiting for the worker rather than inferring."""
        return _ms(self.started_at - self.requested_at)

    def overlap_ms(self, *, vad_started_at: float, vad_ended_at: float) -> int:
        """How much of this execution ran inside the VAD wait. **Measured, not assumed.**

        docs/architecture/audio.md §7: the critical-path contribution of STT is
        `stt_ms - stt_overlap_ms`. Writing the contribution as a constant 0 would erase the
        part that does *not* hide (on CPU, STT is longer than the wait) and push it into
        `unaccounted_ms` as a negative number.
        """
        overlap = min(self.available_at, vad_ended_at) - max(self.requested_at, vad_started_at)
        return max(0, _ms(overlap))


@dataclass(frozen=True, slots=True)
class _Execution:
    generation: int
    requested_at: float
    started_at: float
    finished_at: float
    transcription: Transcription

    @property
    def duration_ms(self) -> int:
        return _ms(self.finished_at - self.started_at)


class SpeculativeStt:
    """Owns every STT execution. **`SPEECH_ENDED` does not start one directly.**

    Splitting the paths would put a second inference in flight the moment a speculation is
    still running, which is the failure single-flight exists to prevent.

    Everything here runs on the event loop and never awaits between a check and the action
    it guards, so the state transitions are atomic without a lock.
    """

    __slots__ = (
        "_capped",
        "_clock",
        "_discarded",
        "_discarded_ms",
        "_done",
        "_max_speculations",
        "_pending",
        "_speculations",
        "_task",
        "_transcribe",
    )

    def __init__(
        self,
        transcribe: Callable[[Samples], Awaitable[Transcription]],
        *,
        max_speculations_per_turn: int = MAX_SPECULATIONS_PER_TURN,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._transcribe = transcribe
        self._max_speculations = max_speculations_per_turn
        self._clock = clock
        self._task: asyncio.Task[_Execution] | None = None
        #: The newest snapshot waiting for the worker. **At most one** — a newer generation
        #: replaces it, and the old audio is released rather than queued
        self._pending: tuple[int, Samples, float] | None = None
        #: The most recent finished execution, waiting to be matched against a confirmed
        #: generation
        self._done: _Execution | None = None
        self._speculations = 0
        self._capped = False
        self._discarded = 0
        self._discarded_ms = 0

    # ── Turn boundaries ──────────────────────────────────────────

    def begin_turn(self) -> None:
        """A new segment started. **Resets the cap, not the running execution.**

        A run from the previous turn may still be in flight and cannot be stopped; it will
        simply fail to match any generation and be counted as discarded.

        **The discard counters are deliberately not reset here.** A turn ends at
        `SPEECH_ENDED`, but its `resolve` runs in a task, and the user can start the next
        utterance while it is still waiting. Clearing the counters at that moment would
        empty them out from under a turn that has not reported yet. They are cleared when
        an outcome carries them away instead.
        """
        self._speculations = 0
        self._capped = False
        self._release_pending()

    # ── Speculation ──────────────────────────────────────────────

    def speculate(self, generation: int, audio: Samples) -> None:
        """Ask for a speculative transcription of `audio`. **Never awaited by the caller.**

        Silently does nothing once the turn is capped: that is the whole point of the cap.
        """
        if self._capped or len(audio) == 0:
            return

        if self._speculations >= self._max_speculations:
            # **One transition.** Flag and pending slot move together, so a worker that
            # comes free right now finds nothing to start.
            self._capped = True
            self._release_pending()
            log.info("stt.speculation_capped", generation=generation, limit=self._max_speculations)
            return

        self._speculations += 1
        requested_at = self._clock()
        log.info("stt.speculation_started", generation=generation, attempt=self._speculations)
        if self._task is None:
            self._start(generation, audio, requested_at)
        else:
            # latest-wins: the older snapshot is dropped, not queued behind this one
            self._pending = (generation, audio, requested_at)

    # ── Adoption ─────────────────────────────────────────────────

    async def resolve(self, generation: int, audio: Samples) -> SttOutcome:
        """The confirmed transcription for `generation`.

        Adopts a matching speculation when there is one, waits for a matching run that is
        still going, and otherwise transcribes `audio` itself. **The result always describes
        the buffer that was actually confirmed.**
        """
        requested_at = self._clock()
        while True:
            if self._done is not None:
                if self._done.generation == generation:
                    execution = self._done
                    self._done = None
                    return self._outcome(execution, speculative=True)
                self._discard_done()

            running = self._task
            if running is not None:
                # Whether it is ours or a stale one, there is nothing to do but wait: the
                # inference cannot be cancelled, and **this wait is what `wait_ms` reports**
                await asyncio.wait({running})
                continue

            # The slot is free, and nothing else can take it without awaiting first, so
            # claiming it here is atomic. **It is claimed rather than run outside the slot**:
            # a speculation for the next turn must not start a second inference.
            task = asyncio.create_task(self._run(generation, audio, requested_at), name="stt")
            self._task = task
            try:
                # No done-callback on this one. The awaiting caller *is* the consumer, and
                # settling it into `_done` as well would leave a result nobody claims.
                execution = await task
            finally:
                if self._task is task:
                    self._task = None
                    self._start_pending()
            return self._outcome(execution, speculative=False)

    # ── Internals ────────────────────────────────────────────────

    def _start(self, generation: int, audio: Samples, requested_at: float) -> None:
        task = asyncio.create_task(self._run(generation, audio, requested_at), name="stt")
        self._task = task
        task.add_done_callback(self._finished)

    async def _run(self, generation: int, audio: Samples, requested_at: float) -> _Execution:
        started_at = self._clock()
        transcription = await self._transcribe(audio)
        return _Execution(
            generation=generation,
            requested_at=requested_at,
            started_at=started_at,
            finished_at=self._clock(),
            transcription=transcription,
        )

    def _finished(self, task: asyncio.Task[_Execution]) -> None:
        """Harvest a speculation and start the next one. **Runs on the event loop.**"""
        if self._task is not task:
            return
        self._task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # **A failed speculation is not a failed turn.** `resolve` will run the confirmed
            # buffer and surface the failure there, where there is someone to report it to.
            log.warning("stt.speculation_failed", error=str(error))
        else:
            if self._done is not None:
                # A newer execution has finished, so the older one can never match again.
                self._discard_done()
            self._done = task.result()

        self._start_pending()

    def _start_pending(self) -> None:
        """Start the waiting snapshot, if the turn still wants one.

        **A capped turn drops it instead of starting it.** That is the half of the cap that
        actually bounds anything: setting a flag without releasing the slot would let the
        worker pick the pending run up the moment it came free.
        """
        pending = self._pending
        self._pending = None
        if pending is None or self._capped:
            return
        self._start(*pending)

    def _discard_done(self) -> None:
        execution = self._done
        if execution is None:
            return
        self._done = None
        self._discarded += 1
        self._discarded_ms += execution.duration_ms
        log.info(
            "stt.speculation_discarded",
            generation=execution.generation,
            duration_ms=execution.duration_ms,
        )

    def _release_pending(self) -> None:
        """Drop the waiting snapshot. **Releases the audio** rather than holding a reference."""
        self._pending = None

    def _outcome(self, execution: _Execution, *, speculative: bool) -> SttOutcome:
        """Build the outcome and **hand the discard counters over with it.**

        Reporting is what clears them, so every discarded execution is counted exactly
        once, against the transcription that was actually waiting when it happened.
        """
        discarded, discarded_ms = self._discarded, self._discarded_ms
        self._discarded = 0
        self._discarded_ms = 0
        return SttOutcome(
            transcription=execution.transcription,
            speculative=speculative,
            requested_at=execution.requested_at,
            started_at=execution.started_at,
            available_at=execution.finished_at,
            discarded_ms=discarded_ms,
            discarded=discarded,
            capped=self._capped,
        )


def _ms(seconds: float) -> int:
    return round(seconds * 1000)
