"""Per-turn latency breakdown. **docs/architecture/audio.md §7.**

The clock is injected, so **no test here waits on real time.** A latency recorder that can
only be verified by actually sleeping is one that won't be verified.
"""

from __future__ import annotations

import pytest

from lumi.agent.latency import SPANS, TurnTimer
from lumi.kernel.ids import CorrelationId


class FakeClock:
    """Advances only when told to."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def timer(clock: FakeClock, *, started_at: float | None = None) -> TurnTimer:
    return TurnTimer(CorrelationId("turn-1"), started_at=started_at, clock=clock)


# ── Spans measure work ───────────────────────────────────────


def test_a_span_measures_only_its_own_work(clock: FakeClock) -> None:
    """★ **Not "since the previous span ended."**

    Boundary-to-boundary can't leave gaps, which is exactly what makes it useless: every
    scheduling delay gets folded into whichever span comes next.
    """
    turn = timer(clock)
    clock.advance(5.0)  # unmeasured work before the span starts
    turn.begin("stt_ms")
    clock.advance(0.22)
    assert turn.end("stt_ms") == 220


def test_the_gaps_become_unaccounted(clock: FakeClock) -> None:
    """**The gaps are the point.** `unaccounted_ms` is the reserve's warning light."""
    turn = timer(clock)
    with turn.span("stt_ms"):
        clock.advance(0.20)
    clock.advance(0.30)  # nobody's span
    with turn.span("assemble_ms"):
        clock.advance(0.03)
    turn.complete()

    latency = turn.finish()
    assert latency.measured_sum_ms == 230
    assert latency.total_ms == 530
    assert latency.unaccounted_ms == 300


def test_the_span_context_manager_records_on_failure(clock: FakeClock) -> None:
    """**A failure took time too.** Dropping it would flatter the percentiles."""
    turn = timer(clock)
    with pytest.raises(RuntimeError), turn.span("stt_ms"):
        clock.advance(0.40)
        raise RuntimeError("boom")

    assert turn.finish().spans["stt_ms"] == 400


def test_the_turn_clock_starts_when_the_user_stopped_talking(clock: FakeClock) -> None:
    """★ **Not when Core noticed.** The gap between the two *is* `vad_ms`."""
    turn = timer(clock, started_at=clock.now - 0.18)
    turn.since_start("vad_ms")
    assert turn.finish().spans["vad_ms"] == 180


def test_an_unknown_span_is_refused(clock: FakeClock) -> None:
    """**The span set is the SLO table.** A typo would silently create a new bucket."""
    with pytest.raises(ValueError, match="Unknown span"):
        timer(clock).begin("llm_ms")


def test_zero_is_recordable(clock: FakeClock) -> None:
    """Phase 1 has no memory retrieval."""
    turn = timer(clock)
    turn.record("retrieve_ms", 0)
    assert turn.finish().spans["retrieve_ms"] == 0


# ── The first one wins ───────────────────────────────────────


def test_reopening_a_finished_span_is_ignored(clock: FakeClock) -> None:
    """The tool loop assembles a prompt every step. **`assemble_ms` means the first one.**"""
    turn = timer(clock)
    with turn.span("assemble_ms"):
        clock.advance(0.03)

    turn.begin("assemble_ms")
    clock.advance(0.50)
    assert turn.end("assemble_ms") is None
    assert turn.finish().spans["assemble_ms"] == 30


def test_ending_a_span_that_never_began_is_ignored(clock: FakeClock) -> None:
    """Lets the caller say "end it" on every token without tracking which was first."""
    assert timer(clock).end("llm_first_token_ms") is None


# ── The measured interval ────────────────────────────────────


def test_the_interval_ends_at_the_first_sound(clock: FakeClock) -> None:
    """★ **Not at the end of the reply.**

    A long answer takes longer to finish speaking, and that is not what p50 is about.
    """
    turn = timer(clock)
    clock.advance(1.0)
    turn.complete()
    clock.advance(9.0)  # the rest of the utterance keeps playing

    latency = turn.finish()
    assert latency.total_ms == 1000
    assert latency.completed


def test_completing_twice_keeps_the_first(clock: FakeClock) -> None:
    turn = timer(clock)
    clock.advance(1.0)
    turn.complete()
    clock.advance(1.0)
    turn.complete()
    assert turn.finish().total_ms == 1000


def test_an_interrupted_turn_ends_where_it_stopped(clock: FakeClock) -> None:
    """Barge-in never reaches the first sound. **The interval ends with the turn.**"""
    turn = timer(clock)
    with turn.span("stt_ms"):
        clock.advance(0.2)
    clock.advance(0.3)

    latency = turn.finish()
    assert not latency.completed
    assert latency.total_ms == 500


# ── Reporting ────────────────────────────────────────────────


def test_unreached_spans_are_omitted_not_zeroed(clock: FakeClock) -> None:
    """★ **"0" and "never got there" are different facts.**

    Recording an interrupted turn's `tts_first_audio_ms` as 0 would drag the percentiles
    toward a speed that never happened.
    """
    turn = timer(clock)
    turn.since_start("vad_ms")

    spans = turn.finish().spans
    assert "vad_ms" in spans
    assert "tts_first_audio_ms" not in spans


def test_unaccounted_may_go_negative(clock: FakeClock) -> None:
    """**Not clamped to zero.** Clamping would hide the bug that produced it."""
    turn = timer(clock)
    turn.record("vad_ms", 5000)
    turn.complete()
    assert turn.finish().unaccounted_ms < 0


def test_the_payload_keeps_the_span_order(clock: FakeClock) -> None:
    """The order in the log is the order things happen in — humans read it."""
    turn = timer(clock)
    for span in reversed(SPANS):
        turn.record(span, 1)

    payload = turn.finish().to_payload()
    assert [key for key in payload if key in SPANS] == list(SPANS)


def test_the_payload_carries_the_correlation_id(clock: FakeClock) -> None:
    """Without it, a measurement can't be tied back to the turn that produced it."""
    assert timer(clock).finish().to_payload()["correlation_id"] == "turn-1"
