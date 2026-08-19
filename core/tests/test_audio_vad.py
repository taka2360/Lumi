"""VAD and barge-in decisions.
**docs/architecture/audio.md test table 2 / 4b / 4c / 5 / 6 / 9 / 12.**

`SpeechSegmenter` is pure logic, so **barge-in is testable with neither a device nor a model.**
"""

from __future__ import annotations

import ast
import statistics
import threading
import time
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from lumi.audio.capture import VadWorker
from lumi.audio.ring import RingBuffer
from lumi.audio.vad import (
    FRAME_MS,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    SileroVad,
    SpeechSegmenter,
    VadEvent,
    VadParams,
)

FRAME = np.zeros(WINDOW_SAMPLES, dtype=np.float32)


def feed(
    segmenter: SpeechSegmenter, probabilities: list[float], *, playing: bool = False
) -> list[VadEvent]:
    events: list[VadEvent] = []
    for probability in probabilities:
        events.extend(segmenter.feed(probability, FRAME, playing=playing))
    return events


def frames_for(ms: int) -> int:
    return ms // FRAME_MS + 1


# ── Mute decision (instant) ────────────────────────────────


def test_mute_fires_on_a_single_frame() -> None:
    """**`min_speech_duration_ms` is never applied to the mute decision** (table 4b).

    Applying it would keep Lumi talking for 250 ms. **That's the worst thing for the experience.**
    """
    segmenter = SpeechSegmenter()
    events = feed(segmenter, [0.9], playing=True)
    assert events == [VadEvent.MUTE_REQUESTED]


def test_mute_does_not_fire_when_nothing_is_playing() -> None:
    """Muting when nothing is playing is meaningless (only segment confirmation proceeds)."""
    segmenter = SpeechSegmenter()
    assert feed(segmenter, [0.9], playing=False) == []


def test_playback_boost_ignores_a_quiet_room(  # table 5
) -> None:
    """**EchoGuard L1.** The threshold rises during playback (0.5 → 0.7)."""
    segmenter = SpeechSegmenter()
    # 0.6 would exceed the threshold when not playing, but not during playback
    assert feed(segmenter, [0.6], playing=True) == []


def test_a_loud_voice_always_interrupts(  # table 6
) -> None:
    """**Confirms there's no suppression.** A loud enough voice can always interrupt, even during
    playback.
    """
    segmenter = SpeechSegmenter()
    assert VadEvent.MUTE_REQUESTED in feed(segmenter, [0.95], playing=True)


def test_a_false_trigger_restores_playback() -> None:
    """**Can recover from a false trigger.**
    This is what lets the mute threshold be pushed aggressively (table 4c).

    "Stop for an instant and resume right away" feels far better than "talk over the user for 300
    ms."
    """
    segmenter = SpeechSegmenter()
    feed(segmenter, [0.9], playing=True)
    assert segmenter.muted

    quiet = [0.0] * frames_for(VadParams().false_trigger_ms)
    events = feed(segmenter, quiet, playing=True)

    assert VadEvent.FALSE_TRIGGER in events
    assert not segmenter.muted


def test_barge_in_works_more_than_once(  # table 4d
) -> None:
    """* **A mute from a confirmed segment doesn't revert automatically.**

    Not being a false trigger, it never takes the `false_trigger` path, so `muted`
    stays set. Left as-is, (a)'s guard means **the next `MUTE_REQUESTED` never fires
    again** — i.e. **barge-in only works once**. This is why a path back
    (`unmute`) is needed.
    """
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    loud = [0.9] * frames_for(params.min_speech_duration_ms)
    silence = [0.0] * frames_for(params.min_silence_duration_ms)

    feed(segmenter, loud + silence, playing=True)
    assert segmenter.muted

    # The next utterance without reverting → no mute fires
    assert VadEvent.MUTE_REQUESTED not in feed(segmenter, [0.95], playing=True)

    segmenter.unmute()
    assert VadEvent.MUTE_REQUESTED in feed(segmenter, [0.95], playing=True)


def test_a_real_utterance_does_not_get_unmuted() -> None:
    """Once a segment is confirmed, the mute is never reverted since it wasn't a false trigger."""
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    loud = [0.9] * frames_for(params.min_speech_duration_ms)
    events = feed(segmenter, loud, playing=True)

    assert VadEvent.MUTE_REQUESTED in events
    assert VadEvent.SPEECH_STARTED in events
    assert VadEvent.FALSE_TRIGGER not in events
    assert segmenter.muted


# ── Speech segment confirmation ──────────────────────────────────────────


def test_speech_needs_a_minimum_duration() -> None:
    """**A single burst of noise never triggers STT.**"""
    segmenter = SpeechSegmenter()
    assert feed(segmenter, [0.9, 0.0, 0.9]) == []


def test_a_single_utterance_starts_and_ends() -> None:
    params = VadParams()
    segmenter = SpeechSegmenter(params)

    events = feed(segmenter, [0.9] * frames_for(params.min_speech_duration_ms))
    assert VadEvent.SPEECH_STARTED in events

    events = feed(segmenter, [0.0] * frames_for(params.min_silence_duration_ms))
    assert VadEvent.SPEECH_ENDED in events
    assert not segmenter.in_speech


def test_a_long_leading_silence_is_ignored() -> None:
    """Table 2. No matter how long the leading silence is, a segment never starts."""
    segmenter = SpeechSegmenter()
    assert feed(segmenter, [0.0] * 200) == []


def test_two_utterances_produce_two_segments() -> None:
    """Table 2. Two consecutive utterances."""
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    speech = [0.9] * frames_for(params.min_speech_duration_ms)
    silence = [0.0] * frames_for(params.min_silence_duration_ms)

    events = feed(segmenter, speech + silence + speech + silence)

    assert events.count(VadEvent.SPEECH_STARTED) == 2
    assert events.count(VadEvent.SPEECH_ENDED) == 2


def test_hysteresis_keeps_the_segment_alive_through_a_dip() -> None:
    """**Table 9.** Never cuts between `exit_threshold`(0.1) and `speech_threshold`(0.3)."""
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    feed(segmenter, [0.9] * frames_for(params.min_speech_duration_ms))

    dipped = feed(segmenter, [0.2] * frames_for(params.min_silence_duration_ms))

    assert VadEvent.SPEECH_ENDED not in dipped
    assert segmenter.in_speech


def test_the_preroll_keeps_the_start_of_a_word() -> None:
    """**Word onsets are never clipped.**
    Frames from before segment confirmation are never discarded.

    There are two "befores" here. Dropping either would lose the word onset.

    | | What | Length |
    |---|---|---|
    | Preroll | **Before** crossing the threshold | `speech_pad_ms` (80 ms) |
    | Candidate | From crossing it **until confirmation** | `min_speech_duration_ms` (250 ms) |
    """
    params = VadParams(speech_pad_ms=96)  # 3 frames' worth
    segmenter = SpeechSegmenter(params)
    speech_frames = frames_for(params.min_speech_duration_ms)

    # The first rising frame is below the threshold (preroll side)
    segmenter.feed(0.1, np.full(WINDOW_SAMPLES, 0.5, dtype=np.float32), playing=False)
    # Above the threshold but not yet confirmed (candidate side)
    for _ in range(speech_frames):
        segmenter.feed(0.9, np.full(WINDOW_SAMPLES, 0.25, dtype=np.float32), playing=False)
    for _ in range(frames_for(params.min_silence_duration_ms)):
        segmenter.feed(0.0, FRAME, playing=False)

    audio = segmenter.take()

    # The preroll's 0.5 remains at the front
    assert float(audio[0]) == pytest.approx(0.5)
    # **The 250 ms (0.25) leading up to confirmation also remains.** Losing this would drop the
    # whole word onset
    assert float(audio[WINDOW_SAMPLES]) == pytest.approx(0.25)
    assert len(audio) >= WINDOW_SAMPLES * (1 + speech_frames)


def test_take_empties_the_segment() -> None:
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    feed(segmenter, [0.9] * frames_for(params.min_speech_duration_ms))
    feed(segmenter, [0.0] * frames_for(params.min_silence_duration_ms))

    assert len(segmenter.take()) > 0
    assert len(segmenter.take()) == 0


# ── Model ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def silero() -> SileroVad:
    return SileroVad()


def test_silence_scores_low(silero: SileroVad) -> None:
    """**Never says "there's a voice" for "nothing was heard."**"""
    probability = silero.probability(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
    assert probability < 0.3


def test_the_window_size_is_enforced(silero: SileroVad) -> None:
    """**Fails instead of guessing** when handed an oddly sized window."""
    with pytest.raises(ValueError, match="512"):
        silero.probability(np.zeros(256, dtype=np.float32))


def test_one_frame_fits_in_the_mute_budget(silero: SileroVad) -> None:
    """Of **mute latency < 50 ms** (table 3), what the implementation controls
    is inference and the decision.

    What's measured here is "from VAD reading a frame to the decision coming out."
    The user's perceived experience (perceived barge-in latency < 120 ms) is a
    separate thing that includes frame boundaries → measured on real hardware in Step F.
    """
    segmenter = SpeechSegmenter()
    frame = np.random.default_rng(0).normal(0, 0.1, WINDOW_SAMPLES).astype(np.float32)

    for _ in range(5):  # Warm-up (the first call carries session initialization)
        silero.probability(frame)

    durations: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        probability = silero.probability(frame)
        segmenter.feed(probability, frame, playing=True)
        durations.append(time.perf_counter() - started)

    assert max(durations) < 0.05, f"p50={statistics.median(durations) * 1000:.2f} ms"


def test_reset_clears_the_lstm_state(silero: SileroVad) -> None:
    """State carrying over across sessions causes misjudgments."""
    noisy = np.random.default_rng(1).normal(0, 0.3, WINDOW_SAMPLES).astype(np.float32)
    silero.probability(noisy)
    silero.reset()
    assert silero.probability(np.zeros(WINDOW_SAMPLES, dtype=np.float32)) < 0.3


# ── Static checks (table 12) ─────────────────────────────────────────

CALLBACK_SOURCES = {
    "capture.py": "_callback",
    "playback.py": "_callback",
}

#: **What must never appear in the audio callback.**
#: Inference, log I/O, sleeping, and acquiring a lock all threaten the deadline (a few ms).
FORBIDDEN_IN_CALLBACK = ("probability", "sleep", "acquire", "info", "warning", "exception")


def callback_ast(filename: str, method: str) -> ast.FunctionDef:
    source = (Path(__file__).resolve().parents[1] / "lumi" / "audio" / filename).read_text(
        encoding="utf-8"
    )
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == method:
            return node
    raise AssertionError(f"{filename}: {method} が見つからない")


@pytest.mark.parametrize(("filename", "method"), list(CALLBACK_SOURCES.items()))
def test_audio_callbacks_do_no_inference_or_io(filename: str, method: str) -> None:
    """**No inference, logging, or locking inside the callback** (table 12).

    Running ONNX inference here would hold the GIL, and the resulting buffer
    underrun would **break even normal playback, let alone barge-in.**
    """
    called: list[str] = []
    for node in ast.walk(callback_ast(filename, method)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            called.append(name)
        if isinstance(node, ast.With | ast.AsyncWith):
            raise AssertionError(f"{filename}: コールバックで with を使わない（ロックの可能性）")

    offenders = [name for name in called if name in FORBIDDEN_IN_CALLBACK]
    assert offenders == [], f"{filename}: {offenders}"


# ── The VAD thread's own failure (table 4b's precondition) ──────────────


class ExplodingVad:
    """Only the shape `VadWorker` uses. **Raises where inference would happen.**"""

    def probability(self, window: np.ndarray) -> float:
        del window
        raise RuntimeError("推論が落ちた")


def test_a_crashing_vad_thread_never_leaves_playback_muted() -> None:
    """★ Regression: **one exception used to end barge-in for the whole session.**

    `_loop` had no handler, so a single raise killed the thread with no log line and no
    listener notification. If it happened while muted, the mute flag stayed set and Lumi
    was silent for good — nothing else clears it.
    """
    ring = RingBuffer(SAMPLE_RATE)
    ring.write(np.zeros(WINDOW_SAMPLES * 4, dtype=np.float32))
    mute_flag = threading.Event()
    mute_flag.set()
    events: list[VadEvent] = []

    worker = VadWorker(
        ring,
        SAMPLE_RATE,
        mute_flag,
        lambda event, _audio, _at: events.append(event),
        vad=cast(SileroVad, ExplodingVad()),
    )
    worker.start()
    worker.stop()

    assert not mute_flag.is_set(), "落ちたスレッドがミュートを握ったままにしない"
    assert events == []
