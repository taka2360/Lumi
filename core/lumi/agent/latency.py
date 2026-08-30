"""Per-turn latency breakdown — **the instrument the SLO is judged with.**

Spans, budgets and the log shape → docs/architecture/audio.md §7 (single source of truth)

## What the measured interval is

**The user stops talking → the first sound is queued.** Not the end of the reply. A long answer
takes longer to finish speaking, and that is not what the p50 target is about
(audio.md §7: "発話終端 → 最初の音が出るまで").

`complete()` closes the interval. A turn cut off by barge-in never calls it, and then the
interval simply ends where the turn did.

## Why spans measure work, not boundaries

Each span times **its own work**, with an explicit begin and end. The alternative — measuring
from wherever the previous span ended — is tempting because it can't leave gaps, and that is
exactly what makes it useless: **every scheduling delay, event publish and await gets folded
into whichever span happens to come next**, and `unaccounted_ms` is always zero by construction.

The gaps are the point. `unaccounted_ms` is the reserve's warning light (0.40 s of the 1.50 s
p50 budget covers DomainEvent persistence, provenance joins, GC, scheduling). It can only warn if
unattributed work actually shows up in it.

## Why the spans no longer simply add up

`stt_ms` runs *inside* `vad_ms` (speculative STT, ADR-039), so the sum of the spans is no
longer the length of the critical path. `critical_path_ms` subtracts the measured overlap,
and `unaccounted_ms` is measured against that.

**The overlap is measured, never assumed.** On CPU, STT takes longer than the VAD wait and
the excess really is on the critical path; writing STT's contribution as a constant 0 would
push that time out of `critical_path_ms` and make `unaccounted_ms` go negative.

## Why the clock is injected

So the tests are deterministic. A latency recorder that can only be tested by actually waiting
is one that won't be tested.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from lumi import logging as lumi_logging
from lumi.agent.stt import SttOutcome
from lumi.kernel.ids import CorrelationId

log = lumi_logging.get_logger(__name__)

#: Structured log event name. **Don't rename it** — measurements are collected by this key
EVENT: Final = "turn_latency"

#: Span order, matching docs/architecture/audio.md §7. **The order is the order they happen in**
SPANS: Final = (
    "vad_ms",
    "stt_ms",
    "retrieve_ms",
    "assemble_ms",
    "llm_first_token_ms",
    "llm_first_segment_ms",
    "tts_first_audio_ms",
    "playback_ms",
)


@dataclass(frozen=True, slots=True)
class Speculation:
    """What speculative STT did this turn (ADR-039). **Facts, not spans.**

    None of these are added to the span sum: `overlap_ms` is subtracted from it, and the
    other two describe work that either happened inside `stt_ms` or was thrown away.
    """

    #: Whether the adopted transcription came from a speculation
    speculative: bool = False
    #: How much of `stt_ms` ran inside `vad_ms`. **Measured from both spans' timestamps**
    overlap_ms: int = 0
    #: Of `stt_ms`, time spent waiting for an uncancellable predecessor rather than inferring
    wait_ms: int = 0
    #: Inference time of executions that were thrown away. **Never added to the critical
    #: path** — whatever part of it delayed this turn is already inside `wait_ms`
    discarded_ms: int = 0
    #: How many executions were thrown away
    discarded: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "stt_speculative": self.speculative,
            "stt_overlap_ms": self.overlap_ms,
            "stt_wait_ms": self.wait_ms,
            "stt_discarded_ms": self.discarded_ms,
            "stt_discarded": self.discarded,
        }


@dataclass(frozen=True, slots=True)
class TurnLatency:
    """One turn's measurement. **Plain data** — reporting is someone else's job."""

    correlation_id: CorrelationId
    spans: Mapping[str, int]
    #: User stopped talking → first sound queued. **Not the end of the reply**
    total_ms: int
    #: Whether the turn reached the first sound at all. `False` means it was cut off
    completed: bool
    speculation: Speculation = Speculation()

    @property
    def measured_sum_ms(self) -> int:
        return sum(self.spans.values())

    @property
    def critical_path_ms(self) -> int:
        """The span sum minus the part that ran inside another span.

        **This, not the sum, is what the p50 budget is compared against**
        (docs/architecture/audio.md §7).
        """
        return self.measured_sum_ms - self.speculation.overlap_ms

    @property
    def unaccounted_ms(self) -> int:
        """Work inside the interval that no span claimed. **The reserve's warning light.**

        **Can go negative** if a span was recorded outside the interval, or if an overlap
        was declared that did not happen. Not clamped to zero: clamping would hide the bug
        that produced it.
        """
        return self.total_ms - self.critical_path_ms

    def to_payload(self) -> dict[str, object]:
        return {
            "correlation_id": str(self.correlation_id),
            **self.spans,
            "measured_sum_ms": self.measured_sum_ms,
            "critical_path_ms": self.critical_path_ms,
            "total_ms": self.total_ms,
            "unaccounted_ms": self.unaccounted_ms,
            "completed": self.completed,
            **self.speculation.to_payload(),
        }


class TurnTimer:
    """Records spans for one turn.

    **An interrupted turn still reports.** Barge-in is the normal case, not an error, and
    "how far did we get before being cut off" is exactly what needs measuring.
    """

    __slots__ = (
        "_clock",
        "_completed_at",
        "_correlation_id",
        "_open",
        "_spans",
        "_speculation",
        "_started",
    )

    def __init__(
        self,
        correlation_id: CorrelationId,
        *,
        started_at: float | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._clock = clock
        self._correlation_id = correlation_id
        #: When the user actually stopped speaking. **Not when Core noticed** — the gap
        #: between the two is `vad_ms`, the first span
        self._started = self._clock() if started_at is None else started_at
        self._spans: dict[str, int] = {}
        self._open: dict[str, float] = {}
        self._speculation = Speculation()
        self._completed_at: float | None = None

    # ── Recording ────────────────────────────────────────────

    def begin(self, span: str) -> None:
        """Start timing `span`. **Ignored if it already finished once.**

        The tool loop assembles a prompt on every step, but `assemble_ms` means **the first**
        assembly — the one the user is waiting on.
        """
        _check(span)
        if span in self._spans:
            return
        self._open[span] = self._clock()

    def end(self, span: str) -> int | None:
        """Close `span`. Returns `None` if it wasn't open (already recorded, or never begun)."""
        _check(span)
        start = self._open.pop(span, None)
        if start is None:
            return None
        elapsed = _ms(self._clock() - start)
        self._spans[span] = elapsed
        return elapsed

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        """Time an enclosed block. **Records even if it raises** — a failure took time too."""
        self.begin(name)
        try:
            yield
        finally:
            self.end(name)

    def record(self, span: str, milliseconds: int) -> None:
        """Write a span measured elsewhere (`vad_ms`) or known to be zero (`retrieve_ms`)."""
        _check(span)
        self._spans.setdefault(span, milliseconds)

    def since_start(self, span: str) -> None:
        """Record `span` as everything from the turn's start until now.

        Only correct for the first span (`vad_ms`), where the turn's start *is* the span's
        start. **Never use it to backfill a later span** — it would swallow every earlier one.
        """
        _check(span)
        self._spans.setdefault(span, _ms(self._clock() - self._started))

    def record_speculation(self, speculation: Speculation) -> None:
        """Attach what speculative STT did. **Does not touch any span.**"""
        self._speculation = speculation

    @property
    def started_at(self) -> float:
        """When the user stopped talking. **The VAD wait starts here** (ADR-039 overlap)."""
        return self._started

    def now(self) -> float:
        """This turn's clock.

        **One turn, one time source.** The overlap between `vad_ms` and `stt_ms` is computed
        from timestamps taken in three places (here, the timer's start, and the STT runner);
        if any of them read a different clock, the subtraction is meaningless — and under a
        fake clock in a test, silently so.
        """
        return self._clock()

    # ── Closing ──────────────────────────────────────────────

    def complete(self) -> None:
        """The first sound is queued. **This is where the measured interval ends.**"""
        if self._completed_at is None:
            self._completed_at = self._clock()

    def finish(self) -> TurnLatency:
        """Close the turn. **Unreached spans are omitted, not zeroed.**

        Zero and "never got there" are different facts. Recording an interrupted turn's
        `tts_first_audio_ms` as 0 would drag the percentiles toward a speed that never happened.
        """
        ended = self._completed_at if self._completed_at is not None else self._clock()
        return TurnLatency(
            correlation_id=self._correlation_id,
            spans={span: self._spans[span] for span in SPANS if span in self._spans},
            total_ms=_ms(ended - self._started),
            completed=self._completed_at is not None,
            speculation=self._speculation,
        )

    def emit(self) -> TurnLatency:
        """Close and log. **Every turn logs** — the SLO is a distribution, not a best case."""
        latency = self.finish()
        log.info(EVENT, **latency.to_payload())
        return latency


def _check(span: str) -> None:
    """**The span set is the SLO table.** A typo would silently create a new bucket."""
    if span not in SPANS:
        raise ValueError(f"Unknown span: {span} (docs/architecture/audio.md §7)")


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def record_stt(timer: TurnTimer, outcome: SttOutcome, *, vad_ended_at: float) -> None:
    """Write `stt_ms` and what speculation did with it.

    **The overlap is measured**, not assumed to be the whole span: on CPU, STT is longer
    than the VAD wait and the remainder is genuinely on the critical path
    (docs/architecture/audio.md §7).
    """
    timer.record("stt_ms", outcome.stt_ms)
    overlap = outcome.overlap_ms(vad_started_at=timer.started_at, vad_ended_at=vad_ended_at)
    timer.record_speculation(
        Speculation(
            speculative=outcome.speculative,
            overlap_ms=overlap,
            wait_ms=outcome.wait_ms,
            discarded_ms=outcome.discarded_ms,
            discarded=outcome.discarded,
        )
    )
    log.info(
        "reactive.stt",
        speculative=outcome.speculative,
        capped=outcome.capped,
        stt_ms=outcome.stt_ms,
        overlap_ms=overlap,
        wait_ms=outcome.wait_ms,
        discarded=outcome.discarded,
    )
