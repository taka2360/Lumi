"""PlaybackScheduler。**docs/architecture/audio.md §6**（先読み並列生成 + 順序保証再生）。

デバイスを開かずに試験できる。`SpeakerPlayback` は `start()` を呼ばなければ
リングと `mute_flag` だけの入れ物であり、それがここで見たいものである。
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
    """`TTSProvider` の形だけ。**遅延を指定できる**（順序保証を試験するため）。"""

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


# ── 順序 ────────────────────────────────────────────────────


async def test_sentences_play_in_order_even_when_the_short_one_finishes_first() -> None:
    """★ **短い文の方が先に生成完了する。** 到着順に再生すると文が入れ替わる。"""
    tts = FakeTts(delays={"ながい文。": 0.05, "みじかい。": 0.0})
    notifier = FakeNotifier()
    scheduler = make(tts, notifier)

    scheduler.speak("ながい文。")
    scheduler.speak("みじかい。")
    await scheduler.finish()

    assert notifier.texts() == ["ながい文。", "みじかい。"]


async def test_generation_is_parallel() -> None:
    """**逐次だと文の切れ目で必ず間が空く。** 4文が生成の直列和より速く終わる。"""
    tts = FakeTts(delays=dict.fromkeys(["A。", "B。", "C。", "D。"], 0.05))
    scheduler = make(tts, FakeNotifier())

    started = asyncio.get_running_loop().time()
    for text in ("A。", "B。", "C。", "D。"):
        scheduler.speak(text)
    await scheduler.finish()

    assert asyncio.get_running_loop().time() - started < 0.05 * 4


# ── 再生と通知 ──────────────────────────────────────────────


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

    # 10 ms = 160 フレーム × 2ch
    assert speaker.queued == 320


async def test_ended_is_sent_once_at_the_end() -> None:
    """**`started` は文ごと、`ended` は1回**（docs/interfaces/renderer.md）。"""
    notifier = FakeNotifier()
    scheduler = make(FakeTts(), notifier)

    scheduler.speak("いち。")
    scheduler.speak("に。")
    await scheduler.finish()

    methods = [m for m, _ in notifier.sent]
    assert methods == [METHOD_SPEECH_STARTED, METHOD_SPEECH_STARTED, METHOD_SPEECH_ENDED]


async def test_nothing_is_sent_when_nothing_was_spoken() -> None:
    """喋っていないのに `ended` を送ると、Stage は開いていない口を閉じにいく。"""
    notifier = FakeNotifier()
    await make(FakeTts(), notifier).finish()
    assert notifier.sent == []


async def test_visemes_are_omitted_without_a_timeline() -> None:
    """**でたらめな時間で口を動かすより動かさない**（docs/interfaces/renderer.md）。"""
    notifier = FakeNotifier()
    scheduler = make(FakeTts(), notifier)

    scheduler.speak("あ。")
    await scheduler.finish()

    assert "spans" not in notifier.sent[0][1]


# ── 中断 ────────────────────────────────────────────────────


async def test_abort_mutes_immediately_and_discards_the_queue() -> None:
    """**生成完了を待たない。** ミュートが先（ユーザーが体感するのはそこだけ）。"""
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
    """**始める前に「まだ意味があるか」を見る**（docs/interfaces/provider.md）。"""
    token = CancelToken()
    token.fire("barge-in")
    tts = FakeTts()
    scheduler = make(tts, FakeNotifier(), cancel_token=token)

    scheduler.speak("もう要らない。")
    outcome = await scheduler.finish()

    assert outcome.spoken == 0


# ── 失敗 ────────────────────────────────────────────────────


async def test_a_failed_sentence_is_counted_not_swallowed() -> None:
    """**1文が喋れなかったことを残す。** 黙って飛ばさない。"""
    notifier = FakeNotifier()
    scheduler = make(FakeTts(fail={"だめ。"}), notifier)

    scheduler.speak("だめ。")
    scheduler.speak("いける。")
    outcome = await scheduler.finish()

    assert outcome.failed == 1
    assert outcome.spoken == 1
    assert notifier.texts() == ["いける。"]


async def test_a_broken_wav_is_counted() -> None:
    """**エンジンの出力を信用しない**（Invariant 3）。"""

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
    """**変換を自作せず失敗させる。** 黙って変換すると音が壊れた理由が分からなくなる。"""

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
