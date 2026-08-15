"""AudioIO — 3層の組み立て。

設計 → docs/architecture/audio.md §2

```
capture stream ─┐
                ├→ [VAD スレッド] ─┬→ mute_flag（同期。★ここで音が止まる）
playback stream ┘                  └→ asyncio キュー（Activity 調停・STT）
```

**入出力は別ストリームで開く**（ADR-020）。duplex の可否はユーザーの機材が決めてしまい、
設計で担保できないものを barge-in の土台にはできない。

**入力デバイスが1つも無いことは正常な状態。** Lumi は起動し、
「音声入力が無い」ことを明示する（TTS 未セットアップと同じ扱い）。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Final

from lumi import logging as lumi_logging
from lumi.audio.capture import MicrophoneCapture, VadWorker
from lumi.audio.devices import AudioPlan
from lumi.audio.playback import SpeakerPlayback
from lumi.audio.ring import Samples
from lumi.audio.vad import SileroVad, VadEvent, VadParams

log = lumi_logging.get_logger(__name__)

#: asyncio 側が詰まったときに溜める上限。**溢れたら古いものを捨てる**
#: （最新の発話の方が価値があり、無限に積むと遅延が増え続ける）
EVENT_QUEUE_SIZE: Final = 32

VadNotification = tuple[VadEvent, Samples | None]


class AudioIO:
    """**組み立てるだけ。** 判断は Reactive Loop（asyncio 側）が行う。"""

    __slots__ = (
        "_capture",
        "_events",
        "_loop",
        "_mute_flag",
        "_params",
        "_plan",
        "_playback",
        "_vad_worker",
    )

    def __init__(self, plan: AudioPlan, *, params: VadParams | None = None) -> None:
        self._plan = plan
        self._mute_flag = threading.Event()
        self._capture = MicrophoneCapture(plan.capture) if plan.capture else None
        self._playback = (
            SpeakerPlayback(plan.playback, mute_flag=self._mute_flag) if plan.playback else None
        )
        self._events: asyncio.Queue[VadNotification] = asyncio.Queue(maxsize=EVENT_QUEUE_SIZE)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._vad_worker: VadWorker | None = None
        self._params = params

    @property
    def can_listen(self) -> bool:
        return self._capture is not None

    @property
    def can_speak(self) -> bool:
        return self._playback is not None

    @property
    def playback(self) -> SpeakerPlayback | None:
        return self._playback

    async def start(self, *, vad: SileroVad | None = None) -> None:
        """**開通するまでを start と呼ぶ。** 開けただけでは「聞けている」と言わない。"""
        self._loop = asyncio.get_running_loop()

        if self._playback is not None:
            self._playback.start()

        if self._capture is None:
            log.warning("audio.no_input", warnings=list(self._plan.warnings))
            return

        self._capture.start()
        await asyncio.to_thread(self._capture.wait_until_open)

        self._vad_worker = VadWorker(
            self._capture.ring,
            self._capture.plan.samplerate,
            self._mute_flag,
            self._notify,
            vad=vad,
            params=self._params,
            is_playing=self._is_playing,
        )
        self._vad_worker.start()
        log.info(
            "audio.started",
            capture=self._capture.plan.describe(),
            playback=self._playback.plan.describe() if self._playback else None,
        )

    def _is_playing(self) -> bool:
        """**EchoGuard L1 の入力。** 再生中は閾値を上げる（抑制はしない）。"""
        return self._playback is not None and self._playback.is_active()

    def _notify(self, event: VadEvent, audio: Samples | None) -> None:
        """**VAD スレッドから呼ばれる。** asyncio に渡すだけで、ここでは何も待たない。"""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._offer, event, audio)

    def _offer(self, event: VadEvent, audio: Samples | None) -> None:
        try:
            self._events.put_nowait((event, audio))
        except asyncio.QueueFull:
            # **最新を優先する。** 古い通知を捨てて、今の発話を通す
            log.warning("audio.event_queue_full")
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
            with suppress(asyncio.QueueFull):
                self._events.put_nowait((event, audio))

    async def events(self) -> AsyncIterator[VadNotification]:
        """asyncio 側の入口。**`_drain_events`（docs/architecture/audio.md §3）に対応する。**"""
        while True:
            yield await self._events.get()

    async def stop(self) -> None:
        if self._vad_worker is not None:
            await asyncio.to_thread(self._vad_worker.stop)
            self._vad_worker = None
        if self._capture is not None:
            self._capture.stop()
        if self._playback is not None:
            self._playback.stop()
