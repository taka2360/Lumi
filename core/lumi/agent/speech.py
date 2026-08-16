"""PlaybackScheduler — 文単位 TTS の先読み並列生成 + 順序保証再生。

設計 → docs/architecture/audio.md §6（`agent/` に置く理由もそこ）

```
文 ─┬→ [TTS 生成 タスク] ─┐
    ├→ [TTS 生成 タスク] ─┼→ FIFO キュー ─→ [再生ループ] ─→ SpeakerPlayback
    └→ [TTS 生成 タスク] ─┘   （順序保証）      + stage.speech.started
```

## なぜ並列生成と順序保証の両方が要るのか

第1文の生成完了を待ってから第2文を始めると、**文の切れ目で必ず間が空く**。
一方、**短い文の方が先に生成完了する**ので、到着順に再生すると文が入れ替わる。
だから「生成は並列、再生は順序」になる。

## 中断

**生成中のタスクを破棄し、再生中のバッファを即ミュートする。生成完了を待たない。**
`abort()` はこの順で行う（ミュートが先。ユーザーが体感するのはそこだけ）。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Final, Protocol

from lumi import logging as lumi_logging
from lumi.audio.playback import SpeakerPlayback
from lumi.audio.resample import pcm16_to_float32, resample, to_interleaved, to_mono
from lumi.audio.ring import Samples
from lumi.audio.wav import WavError, decode_wav
from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import ProviderFailed, ProviderUnavailable
from lumi.providers.tts.base import SpeechAudio, TTSProvider, VoiceConfig
from lumi.transport.protocol import Role

log = lumi_logging.get_logger(__name__)

#: 発話の開始と終了（Core → Stage）。契約 → docs/interfaces/renderer.md
METHOD_SPEECH_STARTED: Final = "stage.speech.started"
METHOD_SPEECH_ENDED: Final = "stage.speech.ended"

#: 同時に走らせる TTS 生成の数〔Provisional〕。docs/architecture/audio.md §6
MAX_PARALLEL: Final = 4

#: 次の文をリングに書き始める前倒し量。**文と文の間に隙間を作らないため**。
#: リングが順序を保証するので、早く書いても音は入れ替わらない
LEAD_S: Final = 0.05


class StageNotifier(Protocol):
    """`WsServer.notify` と同じ形。**テストで差し替えられるようにするため。**"""

    async def notify(
        self, role: Role, method: str, payload: dict[str, Any] | None = None
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SpeechOutcome:
    """**喋れなかったことを黙って捨てない。**"""

    spoken: int
    failed: int
    aborted: bool


@dataclass(slots=True)
class _Slot:
    index: int
    text: str
    audio: asyncio.Future[SpeechAudio | None]


class PlaybackScheduler:
    """1回の発話（Activity 1つ分）に対応する。**使い捨て。**"""

    __slots__ = (
        "_aborted",
        "_cancel_token",
        "_failed",
        "_notifier",
        "_playback",
        "_player",
        "_queue",
        "_semaphore",
        "_spoken",
        "_started",
        "_synth",
        "_total",
        "_tts",
        "_voice",
    )

    def __init__(
        self,
        tts: TTSProvider,
        playback: SpeakerPlayback,
        notifier: StageNotifier,
        *,
        voice: VoiceConfig,
        cancel_token: CancelToken,
        max_parallel: int = MAX_PARALLEL,
    ) -> None:
        self._tts = tts
        self._playback = playback
        self._notifier = notifier
        self._voice = voice
        self._cancel_token = cancel_token
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._queue: asyncio.Queue[_Slot | None] = asyncio.Queue()
        self._synth: set[asyncio.Task[None]] = set()
        self._player: asyncio.Task[None] | None = None
        self._total = 0
        self._spoken = 0
        self._failed = 0
        self._aborted = False
        self._started = False

    def speak(self, text: str) -> None:
        """1文を積む。**待たない。** 生成は裏で並列に走る。"""
        if self._aborted:
            return
        slot = _Slot(index=self._total, text=text, audio=asyncio.get_running_loop().create_future())
        self._total += 1

        task = asyncio.create_task(self._synthesize(slot), name=f"tts-{slot.index}")
        self._synth.add(task)
        task.add_done_callback(self._synth.discard)

        self._queue.put_nowait(slot)
        if self._player is None:
            self._player = asyncio.create_task(self._play_loop(), name="playback")

    async def finish(self) -> SpeechOutcome:
        """もう文は来ない。**最後の音が鳴り終わるまで待つ。**"""
        self._queue.put_nowait(None)
        if self._player is not None:
            await self._player
            self._player = None
        await self._notify_ended()
        return SpeechOutcome(spoken=self._spoken, failed=self._failed, aborted=self._aborted)

    async def abort(self) -> None:
        """**barge-in の出口。** ミュートが先。生成完了を待たない。"""
        if self._aborted:
            return
        self._aborted = True

        # 1. 音を止める（同期。ここだけがユーザーの体感に効く）
        self._playback.mute()
        self._playback.clear()

        # 2. 生成中のタスクを捨てる
        for task in list(self._synth):
            task.cancel()
        if self._player is not None:
            self._player.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._player
            self._player = None

        await self._notify_ended()
        log.info("speech.aborted", spoken=self._spoken, pending=self._total - self._spoken)

    # ── 生成 ──────────────────────────────────────────────

    async def _synthesize(self, slot: _Slot) -> None:
        async with self._semaphore:
            if self._aborted or self._cancel_token.is_set:
                _resolve(slot.audio, None)
                return
            try:
                audio = await self._tts.synthesize(slot.text, self._voice, self._cancel_token)
            except (ProviderFailed, ProviderUnavailable) as error:
                # **1文が喋れなかったことを残す。** 黙って飛ばさない
                log.warning("speech.synthesis_failed", index=slot.index, error=str(error))
                self._failed += 1
                _resolve(slot.audio, None)
            else:
                _resolve(slot.audio, audio)

    # ── 再生 ──────────────────────────────────────────────

    async def _play_loop(self) -> None:
        while True:
            slot = await self._queue.get()
            if slot is None:
                return
            audio = await slot.audio
            if audio is None or self._aborted:
                continue
            await self._play(slot, audio)

    async def _play(self, slot: _Slot, audio: SpeechAudio) -> None:
        try:
            samples, duration_s = self._to_playback(audio.wav)
        except (WavError, ValueError) as error:
            log.warning("speech.wav_failed", index=slot.index, error=str(error))
            self._failed += 1
            return

        if not self._started:
            # **最初の1文の直前でミュートを解いている。**
            # ここより早く解くと、直前の barge-in で立てたミュートを取り消してしまう
            self._playback.unmute()
            self._started = True

        self._playback.write(samples)
        self._spoken += 1
        await self._notify_started(slot.text, audio)
        await asyncio.sleep(max(0.0, duration_s - LEAD_S))

    def _to_playback(self, wav_bytes: bytes) -> tuple[Samples, float]:
        """エンジンの WAV を**出力ストリームの形**に直す。

        **エンジンの出力を信用しない**（Invariant 3）。16bit 以外は明示的に失敗させる。
        """
        wav = decode_wav(wav_bytes)
        if wav.sample_width != 2:
            raise ValueError(f"16bit PCM を期待した（来たのは {wav.sample_width * 8}bit）")

        plan = self._playback.plan
        mono = to_mono(pcm16_to_float32(wav.frames), wav.channels)
        mono = resample(mono, wav.sample_rate, plan.samplerate)
        return to_interleaved(mono, plan.channels), len(mono) / plan.samplerate

    # ── Stage への通知 ────────────────────────────────────

    async def _notify_started(self, text: str, audio: SpeechAudio) -> None:
        payload: dict[str, Any] = {"text": text}
        if audio.timeline is not None:
            payload.update(audio.timeline.to_payload())
        # **タイムラインが無ければビセームを送らない**（口は閉じたまま）。
        # でたらめな時間で動かすより動かさない → docs/interfaces/renderer.md
        await self._notifier.notify(Role.STAGE, METHOD_SPEECH_STARTED, payload)

    async def _notify_ended(self) -> None:
        """**必ず送る。** ここを通らないと Stage の口が開きっぱなしになる。"""
        if self._started:
            await self._notifier.notify(Role.STAGE, METHOD_SPEECH_ENDED, {})


def _resolve(future: asyncio.Future[SpeechAudio | None], value: SpeechAudio | None) -> None:
    """既にキャンセルされている future に set_result しない。"""
    if not future.done():
        future.set_result(value)
