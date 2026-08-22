"""Speculative STT — **ADR-039's test table.**

Every case here runs without a model, a device or an LLM: the runner is handed a fake
transcribe and a fake clock, and generations are fed in by hand. **A speculation engine
that can only be tested by talking into a microphone is one that will not be tested.**

The dangerous failure this file exists for is not "slow". It is **Lumi answering a sentence
the user did not say** — a speculation covering half an utterance finishing first and
winning on speed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import numpy as np
import pytest

from lumi.agent.stt import SpeculativeStt
from lumi.audio.ring import Samples
from lumi.providers.stt.base import Transcription

AUDIO = np.zeros(1600, dtype=np.float32)


def audio_of(length: int) -> Samples:
    return np.zeros(length, dtype=np.float32)


class FakeStt:
    """Transcribes by length, and **only finishes when told to.**

    Holding each execution open is what makes "the result arrives after the end was
    confirmed" and "a stale result arrives late" reproducible instead of timing-dependent.
    """

    def __init__(self, *, auto: bool = True) -> None:
        self.calls: list[int] = []
        self.gates: list[asyncio.Event] = []
        self._auto = auto

    async def transcribe(self, audio: Samples) -> Transcription:
        self.calls.append(len(audio))
        gate = asyncio.Event()
        self.gates.append(gate)
        if self._auto:
            gate.set()
        await gate.wait()
        return Transcription(text=f"len={len(audio)}", language="ja", confidence=1.0, segments=())

    def release(self, index: int = -1) -> None:
        self.gates[index].set()

    def release_all(self) -> None:
        for gate in self.gates:
            gate.set()

    @property
    def running(self) -> int:
        return sum(1 for gate in self.gates if not gate.is_set())


class Clock:
    """A clock that only moves when a test moves it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def settle() -> None:
    """Let done-callbacks and chained executions run. **No wall-clock sleeping.**"""
    for _ in range(6):
        await asyncio.sleep(0)


def runner(
    stt: FakeStt, *, clock: Callable[[], float] | None = None, cap: int = 3
) -> SpeculativeStt:
    return SpeculativeStt(
        stt.transcribe,
        max_speculations_per_turn=cap,
        clock=clock or (lambda: 0.0),
    )


# ── Adoption ───────────────────────────────────────────────


async def test_a_matching_speculation_is_adopted_without_rerunning() -> None:
    stt = FakeStt()
    speculative = runner(stt)
    speculative.begin_turn()

    speculative.speculate(1, AUDIO)
    await settle()

    outcome = await speculative.resolve(1, AUDIO)
    assert outcome.transcription.text == "len=1600"
    assert outcome.speculative is True
    assert len(stt.calls) == 1, "the confirmed buffer must not be transcribed a second time"


async def test_a_result_arriving_after_the_end_is_still_adopted() -> None:
    """**Late is not wrong.** Only a mismatched generation is."""
    stt = FakeStt(auto=False)
    speculative = runner(stt)
    speculative.begin_turn()

    speculative.speculate(1, AUDIO)
    await settle()

    resolving = asyncio.create_task(speculative.resolve(1, AUDIO))
    await settle()
    assert not resolving.done()

    stt.release()
    outcome = await resolving
    assert outcome.speculative is True
    assert len(stt.calls) == 1


async def test_a_stale_generation_is_never_adopted() -> None:
    """★ **The case that would make Lumi answer half a sentence.**

    The speculation covers the first part, the user carries on, and the confirmed buffer is
    longer. The speculation finished first — and is thrown away anyway.
    """
    stt = FakeStt()
    speculative = runner(stt)
    speculative.begin_turn()

    speculative.speculate(1, audio_of(1600))
    await settle()

    outcome = await speculative.resolve(2, audio_of(4800))
    assert outcome.transcription.text == "len=4800", "the confirmed buffer wins, not the fast one"
    assert outcome.speculative is False
    assert outcome.discarded == 1
    assert stt.calls == [1600, 4800]


async def test_no_speculation_at_all_just_runs_it() -> None:
    """A turn with no pause never speculates. **It must still work** (fail-closed)."""
    stt = FakeStt()
    speculative = runner(stt)
    speculative.begin_turn()

    outcome = await speculative.resolve(1, AUDIO)
    assert outcome.speculative is False
    assert outcome.transcription.text == "len=1600"


async def test_a_failed_speculation_falls_back_instead_of_failing_the_turn() -> None:
    class Failing(FakeStt):
        async def transcribe(self, audio: Samples) -> Transcription:
            self.calls.append(len(audio))
            if len(self.calls) == 1:
                raise RuntimeError("model exploded")
            return await super().transcribe(audio)

    stt = Failing()
    speculative = runner(stt)
    speculative.begin_turn()

    speculative.speculate(1, AUDIO)
    await settle()

    outcome = await speculative.resolve(1, AUDIO)
    assert outcome.transcription.text == "len=1600"
    assert outcome.speculative is False


async def test_a_failure_at_the_end_reaches_the_caller() -> None:
    """**The fallback does not swallow.** The turn has someone to report the failure to."""

    class Failing(FakeStt):
        async def transcribe(self, audio: Samples) -> Transcription:
            raise RuntimeError("model exploded")

    speculative = runner(Failing())
    speculative.begin_turn()

    with pytest.raises(RuntimeError):
        await speculative.resolve(1, AUDIO)


# ── Single-flight and boundedness ──────────────────────────


async def test_only_one_execution_runs_at_a_time() -> None:
    """**Advancing the generation must not stack up inferences.**

    STT cannot be cancelled, so anything started is time the machine will spend. The bound
    comes from refusing to start, not from stopping.
    """
    stt = FakeStt(auto=False)
    speculative = runner(stt, cap=10)
    speculative.begin_turn()

    for generation in range(1, 6):
        speculative.speculate(generation, audio_of(generation * 100))
        await settle()
        assert stt.running <= 1

    assert len(stt.calls) == 1, "later generations wait for the worker"


async def test_the_pending_slot_keeps_only_the_newest() -> None:
    """latest-wins. **Intermediate generations are never transcribed at all.**"""
    stt = FakeStt(auto=False)
    speculative = runner(stt, cap=10)
    speculative.begin_turn()

    speculative.speculate(1, audio_of(100))
    await settle()
    speculative.speculate(2, audio_of(200))
    speculative.speculate(3, audio_of(300))
    await settle()

    stt.release(0)
    await settle()

    assert stt.calls == [100, 300], "generation 2 was replaced before it ever started"


async def test_speech_ended_during_a_speculation_does_not_start_a_second() -> None:
    """**`SPEECH_ENDED` goes through the same owner.** A separate path would run two."""
    stt = FakeStt(auto=False)
    speculative = runner(stt)
    speculative.begin_turn()

    speculative.speculate(1, audio_of(100))
    await settle()

    resolving = asyncio.create_task(speculative.resolve(2, audio_of(200)))
    await settle()
    assert stt.running == 1, "the confirmed run waits instead of starting alongside"

    stt.release(0)
    await settle()
    stt.release_all()  # the confirmed run only starts once the worker is free
    outcome = await resolving
    assert outcome.transcription.text == "len=200"
    assert stt.calls == [100, 200]


# ── The per-turn cap ───────────────────────────────────────


async def test_the_cap_stops_speculating_and_runs_once_at_the_end() -> None:
    stt = FakeStt(auto=False)
    speculative = runner(stt, cap=2)
    speculative.begin_turn()

    for generation in range(1, 6):
        speculative.speculate(generation, audio_of(generation * 100))
        await settle()

    assert len(stt.calls) == 1, "only the first one ever started; the rest were capped"

    stt.release(0)
    await settle()
    resolving = asyncio.create_task(speculative.resolve(9, audio_of(900)))
    await settle()
    stt.release_all()
    outcome = await resolving
    assert outcome.capped is True
    assert outcome.speculative is False
    assert stt.calls == [100, 900]


async def test_hitting_the_cap_drops_the_pending_run_in_the_same_step() -> None:
    """★ **The half of the cap that actually bounds anything.**

    Setting the flag without releasing the slot lets the worker come free an instant later
    and start a run for a turn that had already given up — the "capped" turn quietly runs
    one more inference than the cap allows.
    """
    stt = FakeStt(auto=False)
    speculative = runner(stt, cap=2)
    speculative.begin_turn()

    speculative.speculate(1, audio_of(100))
    await settle()
    speculative.speculate(2, audio_of(200))  # pending
    speculative.speculate(3, audio_of(300))  # hits the cap
    await settle()

    stt.release(0)
    await settle()

    assert stt.calls == [100], "the pending run must not start after the cap"


async def test_the_cap_resets_between_turns() -> None:
    stt = FakeStt()
    speculative = runner(stt, cap=1)
    speculative.begin_turn()

    speculative.speculate(1, audio_of(100))
    speculative.speculate(2, audio_of(200))
    await settle()
    await speculative.resolve(2, audio_of(200))

    speculative.begin_turn()
    speculative.speculate(3, audio_of(300))
    await settle()
    outcome = await speculative.resolve(3, audio_of(300))

    assert outcome.speculative is True
    assert outcome.capped is False


# ── Measurement ────────────────────────────────────────────


async def test_the_span_starts_when_the_run_was_requested() -> None:
    """**Waiting for an uncancellable predecessor belongs to someone.**

    Starting the clock when inference begins would make the wait disappear from every
    measurement, which is exactly the delay speculation can introduce.
    """
    clock = Clock()
    stt = FakeStt(auto=False)
    speculative = runner(stt, clock=clock, cap=10)
    speculative.begin_turn()

    speculative.speculate(1, audio_of(100))
    await settle()

    clock.advance(0.5)
    resolving = asyncio.create_task(speculative.resolve(2, audio_of(200)))
    await settle()

    clock.advance(0.3)  # the stale run is still going
    stt.release(0)
    await settle()
    clock.advance(0.2)  # the confirmed run
    stt.release(1)

    outcome = await resolving
    assert outcome.wait_ms == 300, "the wait for the worker is inside stt_ms"
    assert outcome.stt_ms == 500


async def test_discarded_time_is_reported_separately() -> None:
    """`stt_discarded_ms` never joins the critical path.

    Whatever part of a discarded run actually delayed this turn is already inside
    `stt_wait_ms`; adding both would count it twice.
    """
    clock = Clock()
    stt = FakeStt(auto=False)
    speculative = runner(stt, clock=clock, cap=10)
    speculative.begin_turn()

    speculative.speculate(1, audio_of(100))
    await settle()
    clock.advance(0.4)
    stt.release(0)
    await settle()

    resolving = asyncio.create_task(speculative.resolve(2, audio_of(200)))
    await settle()
    stt.release_all()
    outcome = await resolving
    assert outcome.discarded == 1
    assert outcome.discarded_ms == 400
    assert outcome.stt_ms == 0, "the confirmed run itself took no time on this clock"


async def test_the_overlap_is_measured_not_assumed() -> None:
    """On CPU, STT is longer than the VAD wait. **The excess is on the critical path.**"""
    clock = Clock()
    stt = FakeStt(auto=False)
    speculative = runner(stt, clock=clock)
    speculative.begin_turn()

    speculative.speculate(1, AUDIO)  # requested at 0.0
    await settle()
    clock.advance(0.9)  # a slow CPU transcription
    stt.release(0)
    await settle()

    outcome = await speculative.resolve(1, AUDIO)
    # The VAD wait ran from 0.0 to 0.43; the run finished at 0.9
    assert outcome.overlap_ms(vad_started_at=0.0, vad_ended_at=0.43) == 430
    assert outcome.stt_ms - outcome.overlap_ms(vad_started_at=0.0, vad_ended_at=0.43) == 470


async def test_a_fully_hidden_speculation_contributes_nothing() -> None:
    clock = Clock()
    stt = FakeStt(auto=False)
    speculative = runner(stt, clock=clock)
    speculative.begin_turn()

    speculative.speculate(1, AUDIO)
    await settle()
    clock.advance(0.06)  # a GPU transcription
    stt.release(0)
    await settle()

    outcome = await speculative.resolve(1, AUDIO)
    assert outcome.stt_ms == 60
    assert outcome.overlap_ms(vad_started_at=0.0, vad_ended_at=0.43) == 60


async def test_a_capped_turn_reports_no_overlap() -> None:
    """It starts after the end is confirmed, so **there is nothing to hide it behind.**"""
    clock = Clock()
    stt = FakeStt()
    speculative = runner(stt, clock=clock, cap=0)
    speculative.begin_turn()

    speculative.speculate(1, AUDIO)
    await settle()
    assert stt.calls == []

    clock.advance(0.43)
    outcome = await speculative.resolve(1, AUDIO)
    assert outcome.speculative is False
    assert outcome.overlap_ms(vad_started_at=0.0, vad_ended_at=0.43) == 0
