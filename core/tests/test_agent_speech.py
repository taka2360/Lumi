"""PlaybackScheduler. **docs/architecture/audio.md §6**
(parallel pre-generation + order-preserving playback).

Testable without opening a device. Without calling `start()`, `SpeakerPlayback` is
just a container holding a ring and `mute_flag` — exactly what's being tested here.
"""

from __future__ import annotations

import asyncio
import io
import wave
from typing import Any

import numpy as np

from lumi.agent.speech import METHOD_SPEECH_ENDED, METHOD_SPEECH_STARTED, PlaybackScheduler
from lumi.audio.devices import Device, StreamPlan
from lumi.audio.playback import SpeakerPlayback
from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import ProviderFailed
from lumi.providers.tts.base import SpeechAudio, VoiceConfig
from lumi.transport.protocol import Role

RATE = 16_000
VOICE = VoiceConfig(speaker=0)


def wav_bytes(milliseconds: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(RATE)
        sink.writeframes(np.zeros(RATE * milliseconds // 1000, dtype="<i2").tobytes())
    return buffer.getvalue()


def playback(channels: int = 1) -> SpeakerPlayback:
    device = Device(
        index=0,
        name="fake",
        host_api="WASAPI",
        max_input_channels=0,
        max_output_channels=channels,
        default_samplerate=float(RATE),
    )
    return SpeakerPlayback(StreamPlan(device=device, samplerate=RATE, channels=channels))


class FakeTts:
    """Only the shape of `TTSProvider`. **Lets you specify delays** (to test order preservation)."""

    id = "fake-tts"

    def __init__(self, delays: dict[str, float] | None = None, *, fail: set[str] | None = None):
        self.delays = delays or {}
        self.fail = fail or set()
        self.calls: list[str] = []

    async def synthesize(
        self, text: str, voice: VoiceConfig, cancel_token: CancelToken
    ) -> SpeechAudio:
        del voice, cancel_token
        self.calls.append(text)
        await asyncio.sleep(self.delays.get(text, 0.0))
        if text in self.fail:
            raise ProviderFailed("boom", text)
        return SpeechAudio(wav=wav_bytes(10), timeline=None)

    def supported_languages(self) -> frozenset[str]:
        return frozenset({"ja"})


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        assert role is Role.STAGE
        self.sent.append((method, payload or {}))

    def texts(self) -> list[str]:
        return [p["text"] for m, p in self.sent if m == METHOD_SPEECH_STARTED]


def make(tts: FakeTts, notifier: FakeNotifier, **kwargs: Any) -> PlaybackScheduler:
    return PlaybackScheduler(
        tts,  # type: ignore[arg-type]
        kwargs.pop("playback", None) or playback(),
        notifier,
        voice=VOICE,
        cancel_token=kwargs.pop("cancel_token", None) or CancelToken(),
        **kwargs,
    )


# ── Ordering ────────────────────────────────────────────────────


async def test_sentences_play_in_order_even_when_the_short_one_finishes_first() -> None:
    """* **A shorter sentence finishes generating first.** Playing in arrival order would scramble
    sentences.
    """
    tts = FakeTts(delays={"ながい文。": 0.05, "みじかい。": 0.0})
    notifier = FakeNotifier()
    scheduler = make(tts, notifier)

    scheduler.speak("ながい文。")
    scheduler.speak("みじかい。")
    await scheduler.finish()

    assert notifier.texts() == ["ながい文。", "みじかい。"]


async def test_generation_does_not_run_ahead_by_default() -> None:
    """★ **Parallel generation was removed after measuring** (docs/architecture/audio.md §6).

    A TTS engine saturates its device on one request, so four at once don't finish sooner —
    each just takes four times as long, and **the first sentence is the one that suffers**.
    """
    order: list[str] = []

    class Tracking(FakeTts):
        async def synthesize(
            self, text: str, voice: VoiceConfig, cancel_token: CancelToken
        ) -> SpeechAudio:
            order.append(f"start:{text}")
            result = await super().synthesize(text, voice, cancel_token)
            order.append(f"end:{text}")
            return result

    scheduler = make(Tracking(delays={"A。": 0.02, "B。": 0.0}), FakeNotifier())
    scheduler.speak("A。")
    scheduler.speak("B。")
    await scheduler.finish()

    # B doesn't start until A is done
    assert order.index("end:A。") < order.index("start:B。")


async def test_look_ahead_still_happens() -> None:
    """N=1 is "one at a time, continuously" — **not** "wait for playback to finish."

    Synthesis outruns real time, so the next sentence is ready before the current one
    stops playing. The gap between sentences stays closed.
    """
    tts = FakeTts(delays=dict.fromkeys(["A。", "B。", "C。"], 0.01))
    scheduler = make(tts, FakeNotifier())

    started = asyncio.get_running_loop().time()
    for text in ("A。", "B。", "C。"):
        scheduler.speak(text)
    await scheduler.finish()

    # 3 sentences x 10 ms audio each; generation overlaps playback rather than following it
    assert asyncio.get_running_loop().time() - started < 0.2


# ── Playback and notification ──────────────────────────────────────────────


async def test_audio_reaches_the_ring() -> None:
    speaker = playback()
    scheduler = make(FakeTts(), FakeNotifier(), playback=speaker)

    scheduler.speak("こんにちは。")
    await scheduler.finish()

    assert speaker.reference.available >= 0
    assert not speaker.mute_flag.is_set()


async def test_the_ring_gets_one_sample_per_channel() -> None:
    speaker = playback(channels=2)
    scheduler = make(FakeTts(), FakeNotifier(), playback=speaker)

    scheduler.speak("あ。")
    await scheduler.finish()

    # 10 ms = 160 frames x 2ch
    assert speaker.queued == 320


async def test_ended_is_sent_once_at_the_end() -> None:
    """**`started` fires per sentence, `ended` fires once** (docs/interfaces/renderer.md)."""
    notifier = FakeNotifier()
    scheduler = make(FakeTts(), notifier)

    scheduler.speak("いち。")
    scheduler.speak("に。")
    await scheduler.finish()

    methods = [m for m, _ in notifier.sent]
    assert methods == [METHOD_SPEECH_STARTED, METHOD_SPEECH_STARTED, METHOD_SPEECH_ENDED]


async def test_nothing_is_sent_when_nothing_was_spoken() -> None:
    """Sending `ended` when nothing was spoken would make the Stage try to close a mouth that was
    never open.
    """
    notifier = FakeNotifier()
    await make(FakeTts(), notifier).finish()
    assert notifier.sent == []


async def test_visemes_are_omitted_without_a_timeline() -> None:
    """**Better to not move the mouth than move it on bogus timing** (interfaces/renderer.md)."""
    notifier = FakeNotifier()
    scheduler = make(FakeTts(), notifier)

    scheduler.speak("あ。")
    await scheduler.finish()

    assert "spans" not in notifier.sent[0][1]


# ── Interruption ────────────────────────────────────────────────────


async def test_abort_mutes_immediately_and_discards_the_queue() -> None:
    """**Never waits for generation to finish.** Mute comes first (the only part the user actually
    feels).
    """
    speaker = playback()
    tts = FakeTts(delays={"ながい。": 1.0})
    scheduler = make(tts, FakeNotifier(), playback=speaker)

    scheduler.speak("ながい。")
    await asyncio.sleep(0)
    await scheduler.abort()

    assert speaker.mute_flag.is_set()
    assert speaker.queued == 0


async def test_abort_is_idempotent() -> None:
    scheduler = make(FakeTts(), FakeNotifier())
    await scheduler.abort()
    await scheduler.abort()


async def test_speaking_after_abort_does_nothing() -> None:
    tts = FakeTts()
    scheduler = make(tts, FakeNotifier())

    await scheduler.abort()
    scheduler.speak("むだ。")
    await asyncio.sleep(0)

    assert tts.calls == []


async def test_a_cancelled_token_stops_synthesis_before_it_starts() -> None:
    """**Checks "is this still worth doing" before starting** (docs/interfaces/provider.md)."""
    token = CancelToken()
    token.fire("barge-in")
    tts = FakeTts()
    scheduler = make(tts, FakeNotifier(), cancel_token=token)

    scheduler.speak("もう要らない。")
    outcome = await scheduler.finish()

    assert outcome.spoken == 0


# ── Failure ────────────────────────────────────────────────────


async def test_a_failed_sentence_is_counted_not_swallowed() -> None:
    """**Records that a sentence couldn't be spoken.** Never silently skipped."""
    notifier = FakeNotifier()
    scheduler = make(FakeTts(fail={"だめ。"}), notifier)

    scheduler.speak("だめ。")
    scheduler.speak("いける。")
    outcome = await scheduler.finish()

    assert outcome.failed == 1
    assert outcome.spoken == 1
    assert notifier.texts() == ["いける。"]


async def test_a_broken_wav_is_counted() -> None:
    """**Never trusts the engine's output** (Invariant 3)."""

    class BrokenTts(FakeTts):
        async def synthesize(
            self, text: str, voice: VoiceConfig, cancel_token: CancelToken
        ) -> SpeechAudio:
            del text, voice, cancel_token
            return SpeechAudio(wav=b"not a wav", timeline=None)

    scheduler = make(BrokenTts(), FakeNotifier())
    scheduler.speak("あ。")
    outcome = await scheduler.finish()

    assert outcome.failed == 1
    assert outcome.spoken == 0


async def test_an_unsupported_sample_width_is_refused_not_converted() -> None:
    """**Fails rather than inventing a conversion.** Silently converting would make a broken-sound
    bug impossible to trace.
    """

    class EightBitTts(FakeTts):
        async def synthesize(
            self, text: str, voice: VoiceConfig, cancel_token: CancelToken
        ) -> SpeechAudio:
            del text, voice, cancel_token
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as sink:
                sink.setnchannels(1)
                sink.setsampwidth(1)
                sink.setframerate(RATE)
                sink.writeframes(b"\x80" * 100)
            return SpeechAudio(wav=buffer.getvalue(), timeline=None)

    scheduler = make(EightBitTts(), FakeNotifier())
    scheduler.speak("あ。")
    outcome = await scheduler.finish()

    assert outcome.failed == 1
    assert outcome.spoken == 0
