"""PlaybackScheduler — parallel pre-generation of sentence-level TTS + order-preserving playback.

Design → docs/architecture/audio.md §6 (also explains why this lives under `agent/`)

```
sentence ─┬→ [TTS generation task] ─┐
          ├→ [TTS generation task] ─┼→ FIFO queue ─→ [playback loop] ─→ SpeakerPlayback
          └→ [TTS generation task] ─┘  (order preserved)  + stage.speech.started
```

## Why both parallel generation and order preservation are needed

Waiting for the first sentence to finish generating before starting the second **always
leaves a gap between sentences**. On the other hand, **shorter sentences finish generating
first**, so playing them in arrival order would scramble sentence order.
Hence "generate in parallel, play in order."

## Interruption

**Discard in-progress generation tasks and mute the playback buffer immediately. Don't wait
for generation to finish.**
`abort()` does this in that order (mute first — that's the only part the user actually feels).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Final, Protocol

from lumi import logging as lumi_logging
from lumi.agent.latency import TurnTimer
from lumi.audio.playback import SpeakerPlayback
from lumi.audio.resample import pcm16_to_float32, resample, to_interleaved, to_mono
from lumi.audio.ring import Samples
from lumi.audio.wav import WavError, decode_wav
from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import ProviderFailed, ProviderUnavailable
from lumi.providers.tts.base import SpeechAudio, TTSProvider, VoiceConfig
from lumi.tasks import spawn
from lumi.transport.methods import METHOD_SPEECH_ENDED, METHOD_SPEECH_STARTED
from lumi.transport.protocol import Role

log = lumi_logging.get_logger(__name__)

#: Number of TTS generations to run concurrently [Provisional]. docs/architecture/audio.md §6
MAX_PARALLEL: Final = 1

# : How far ahead of time to start writing the next sentence into the ring. **Keeps no gap between
# sentences**. : Since the ring preserves order, writing early doesn't scramble the audio
LEAD_S: Final = 0.05


class StageNotifier(Protocol):
    """Same shape as `WsServer.notify`. **Exists so tests can substitute it.**"""

    async def notify(
        self, role: Role, method: str, payload: dict[str, Any] | None = None
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SpeechOutcome:
    """**Never silently discard the fact that something couldn't be spoken.**"""

    spoken: int
    failed: int
    aborted: bool


@dataclass(slots=True)
class _Slot:
    index: int
    text: str
    audio: asyncio.Future[SpeechAudio | None]


class PlaybackScheduler:
    """Corresponds to one utterance (one Activity). **Single-use.**"""

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
        "_timer",
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
        timer: TurnTimer | None = None,
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
        #: Optional. **Only the first sentence contributes** to the SLO spans — that is the
        #: one the user is waiting on (docs/architecture/audio.md §7)
        self._timer = timer

    def speak(self, text: str) -> None:
        """Queue one sentence. **Non-blocking.** Generation runs in parallel in the background."""
        if self._aborted:
            return
        slot = _Slot(index=self._total, text=text, audio=asyncio.get_running_loop().create_future())
        self._total += 1

        spawn(
            self._synthesize(slot),
            name=f"tts-{slot.index}",
            event="speech.synthesis_crashed",
            keep=self._synth,
        )

        if slot.index == 0 and self._timer is not None:
            self._timer.begin("tts_first_audio_ms")
        self._queue.put_nowait(slot)
        if self._player is None:
            self._player = spawn(
                self._play_loop(), name="playback", event="speech.playback_crashed"
            )

    async def finish(self) -> SpeechOutcome:
        """No more sentences will come. **Wait until the last sound finishes playing.**"""
        self._queue.put_nowait(None)
        if self._player is not None:
            await self._player
            self._player = None
        await self._notify_ended()
        return SpeechOutcome(spoken=self._spoken, failed=self._failed, aborted=self._aborted)

    async def abort(self) -> None:
        """**The barge-in exit point.** Mute first. Don't wait for generation to finish."""
        if self._aborted:
            return
        self._aborted = True

        # 1. Stop sound (synchronous. This is the only part the user actually feels)
        self._playback.mute()
        self._playback.clear()

        # 2. Discard in-progress generation tasks
        for task in list(self._synth):
            task.cancel()
        if self._player is not None:
            self._player.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._player
            self._player = None

        await self._notify_ended()
        log.info("speech.aborted", spoken=self._spoken, pending=self._total - self._spoken)

    # ── Generation ──────────────────────────────────────────────

    async def _synthesize(self, slot: _Slot) -> None:
        # **The slot is settled in `finally`, whatever happens.** `_play_loop` awaits this
        # future and `finish()` awaits `_play_loop`, so one unresolved slot hangs the whole
        # turn — and a Provider raising anything but the two below (`ProviderNotConfigured`,
        # a library bug) is exactly the case that used to leave it unresolved
        try:
            async with self._semaphore:
                if self._aborted or self._cancel_token.is_set:
                    return
                audio = await self._tts.synthesize(slot.text, self._voice, self._cancel_token)
                if slot.index == 0 and self._timer is not None:
                    self._timer.end("tts_first_audio_ms")
                    self._timer.begin("playback_ms")
                _resolve(slot.audio, audio)
        except (ProviderFailed, ProviderUnavailable) as error:
            # **Record that this sentence couldn't be spoken.** Don't silently skip it
            log.warning("speech.synthesis_failed", index=slot.index, error=str(error))
            self._failed += 1
        except Exception:
            # Not a failure mode the Provider contract names. **Still counted, never swallowed**
            log.exception("speech.synthesis_crashed", index=slot.index)
            self._failed += 1
        finally:
            _resolve(slot.audio, None)

    # ── Playback ──────────────────────────────────────────────

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
            # **Unmute right before the first sentence.**
            # Unmuting any earlier would cancel a mute set by a preceding barge-in
            self._playback.unmute()
            self._started = True

        self._playback.write(samples)
        if slot.index == 0 and self._timer is not None:
            self._timer.end("playback_ms")
            # ★ The first sound is queued. **This is where the measured interval ends**
            self._timer.complete()
        self._spoken += 1
        await self._notify_started(slot.text, audio)
        await asyncio.sleep(max(0.0, duration_s - LEAD_S))

    def _to_playback(self, wav_bytes: bytes) -> tuple[Samples, float]:
        """Convert the engine's WAV into **the output stream's format**.

        **Don't trust the engine's output** (Invariant 3). Fail explicitly on anything but 16-bit.
        """
        wav = decode_wav(wav_bytes)
        if wav.sample_width != 2:
            raise ValueError(f"Expected 16-bit PCM (got {wav.sample_width * 8}-bit)")

        plan = self._playback.plan
        mono = to_mono(pcm16_to_float32(wav.frames), wav.channels)
        mono = resample(mono, wav.sample_rate, plan.samplerate)
        return to_interleaved(mono, plan.channels), len(mono) / plan.samplerate

    # ── Notifying the Stage ────────────────────────────────────

    async def _notify_started(self, text: str, audio: SpeechAudio) -> None:
        payload: dict[str, Any] = {"text": text}
        if audio.timeline is not None:
            payload.update(audio.timeline.to_payload())
        # **Don't send visemes if there's no timeline** (mouth stays closed).
        # Not moving is better than moving on bogus timing → docs/interfaces/renderer.md
        await self._notifier.notify(Role.STAGE, METHOD_SPEECH_STARTED, payload)

    async def _notify_ended(self) -> None:
        """**Always send this.** Skipping it leaves the Stage's mouth stuck open."""
        if self._started:
            await self._notifier.notify(Role.STAGE, METHOD_SPEECH_ENDED, {})


def _resolve(future: asyncio.Future[SpeechAudio | None], value: SpeechAudio | None) -> None:
    """Don't call set_result on a future that's already been cancelled."""
    if not future.done():
        future.set_result(value)
