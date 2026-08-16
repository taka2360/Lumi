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

The gaps are the point. `unaccounted_ms` is the reserve's warning light (0.13 s of the 1.20 s
budget covers DomainEvent persistence, provenance joins, GC, scheduling). It can only warn if
unattributed work actually shows up in it.

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
class TurnLatency:
    """One turn's measurement. **Plain data** — reporting is someone else's job."""

    correlation_id: CorrelationId
    spans: Mapping[str, int]
    #: User stopped talking → first sound queued. **Not the end of the reply**
    total_ms: int
    #: Whether the turn reached the first sound at all. `False` means it was cut off
    completed: bool

    @property
    def measured_sum_ms(self) -> int:
        return sum(self.spans.values())

    @property
    def unaccounted_ms(self) -> int:
        """Work inside the interval that no span claimed. **The reserve's warning light.**

        **Can go negative** if a span was recorded outside the interval. Not clamped to zero:
        clamping would hide the bug that produced it.
        """
        return self.total_ms - self.measured_sum_ms

    def to_payload(self) -> dict[str, object]:
        return {
            "correlation_id": str(self.correlation_id),
            **self.spans,
            "measured_sum_ms": self.measured_sum_ms,
            "total_ms": self.total_ms,
            "unaccounted_ms": self.unaccounted_ms,
            "completed": self.completed,
        }


class TurnTimer:
    """Records spans for one turn.

    **An interrupted turn still reports.** Barge-in is the normal case, not an error, and
    "how far did we get before being cut off" is exactly what needs measuring.
    """

    __slots__ = ("_clock", "_completed_at", "_correlation_id", "_open", "_spans", "_started")

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
        )

    def emit(self) -> TurnLatency:
        """Close and log. **Every turn logs** — the SLO is a distribution, not a best case."""
        latency = self.finish()
        log.info(EVENT, **latency.to_payload())
        return latency


def _check(span: str) -> None:
    """**The span set is the SLO table.** A typo would silently create a new bucket."""
    if span not in SPANS:
        raise ValueError(f"未知の区間: {span}（docs/architecture/audio.md §7）")


def _ms(seconds: float) -> int:
    return round(seconds * 1000)
