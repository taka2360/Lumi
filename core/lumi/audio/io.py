"""AudioIO — assembles the 3 layers.

Design → docs/architecture/audio.md §2

```
capture stream ─┐
                ├→ [VAD thread] ─┬→ mute_flag (synchronous. * sound stops here)
playback stream ┘                └→ asyncio queue (Activity arbitration / STT)
```

**Input and output are opened as separate streams** (ADR-020). Whether duplex is
possible is up to the user's hardware, and barge-in can't be built on something the
design can't guarantee.

**Having zero input devices is a normal state.** Lumi starts up and explicitly reports
"no voice input" (treated the same as TTS not being set up).
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

#: Cap on how much to buffer when the asyncio side is backed up. **Drop the oldest on overflow**
#: (the most recent utterance is the valuable one; queuing forever only grows latency)
EVENT_QUEUE_SIZE: Final = 32

VadNotification = tuple[VadEvent, Samples | None]


class AudioIO:
    """**Wiring only.** Decisions are made by the Reactive Loop (on the asyncio side)."""

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
        """**"start" means until the connection is actually up.** Just opening it doesn't mean "listening."""
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

    def resume_listening(self) -> None:
        """**Resets to a state that can accept the next barge-in.**

        A mute from a confirmed segment isn't a false trigger, so it doesn't revert
        automatically. Without calling this, **barge-in only works once** (`VadWorker.resume`).
        """
        if self._vad_worker is not None:
            self._vad_worker.resume()

    def _is_playing(self) -> bool:
        """**Input to EchoGuard L1.** Raises the threshold during playback (never suppresses)."""
        return self._playback is not None and self._playback.is_active()

    def _notify(self, event: VadEvent, audio: Samples | None) -> None:
        """**Called from the VAD thread.** Just hands off to asyncio; nothing is awaited here."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._offer, event, audio)

    def _offer(self, event: VadEvent, audio: Samples | None) -> None:
        try:
            self._events.put_nowait((event, audio))
        except asyncio.QueueFull:
            # **Prioritize the latest.** Drop the old notification to let the current utterance through
            log.warning("audio.event_queue_full")
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
            with suppress(asyncio.QueueFull):
                self._events.put_nowait((event, audio))

    async def events(self) -> AsyncIterator[VadNotification]:
        """The entry point on the asyncio side. **Corresponds to `_drain_events` (docs/architecture/audio.md §3).**"""
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
