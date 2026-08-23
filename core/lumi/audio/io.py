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
from lumi.audio.capture import CaptureUnavailable, MicrophoneCapture, VadWorker
from lumi.audio.devices import AudioPlan
from lumi.audio.playback import SpeakerPlayback
from lumi.audio.vad import SileroVad, VadNotification, VadParams

log = lumi_logging.get_logger(__name__)

#: Cap on how much to buffer when the asyncio side is backed up. **Drop the oldest on overflow**
#: (the most recent utterance is the valuable one; queuing forever only grows latency)
EVENT_QUEUE_SIZE: Final = 32


class AudioIO:
    """**Wiring only.** Decisions are made by the Reactive Loop (on the asyncio side)."""

    __slots__ = (
        "_capture",
        "_events",
        "_input_muted",
        "_loop",
        "_mute_flag",
        "_params",
        "_plan",
        "_playback",
        "_vad",
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
        #: Kept across a mute so unmuting does not reload the model.
        self._vad: SileroVad | None = None
        #: **Muting closes the input stream**, so this is not "ignore what arrives" — see
        #: `set_input_muted`.
        self._input_muted = False

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
        """**"start" means until the connection is actually up.** Just opening it doesn't mean
        "listening.
        """
        self._loop = asyncio.get_running_loop()

        if self._playback is not None:
            self._playback.start()

        if self._capture is None:
            log.warning("audio.no_input", warnings=list(self._plan.warnings))
            return

        self._vad = vad
        self._capture.start()
        await asyncio.to_thread(self._capture.wait_until_open)
        self._start_vad()
        log.info(
            "audio.started",
            capture=self._capture.plan.describe(),
            playback=self._playback.plan.describe() if self._playback else None,
        )

    def _start_vad(self) -> None:
        """A worker over the current stream. **A fresh one every time**: `VadWorker.stop`
        latches, so restarting the old one would return without reading a single frame.

        **The model is kept but its state is not.** Silero carries context across frames,
        and a stream that resumes minutes later is not a continuation of the one before —
        feeding it as though it were makes the first windows after an unmute be judged
        against audio from before it.
        """
        assert self._capture is not None
        if self._vad is not None:
            self._vad.reset()
        self._vad_worker = VadWorker(
            self._capture.ring,
            self._capture.plan.samplerate,
            self._mute_flag,
            self._notify,
            vad=self._vad,
            params=self._params,
            is_playing=self._is_playing,
        )
        self._vad_worker.start()
        # **Held for the next unmute.** `VadWorker.start` builds one when given none, and
        # loading Silero again on every unmute would make the button feel broken.
        self._vad = self._vad_worker.vad

    @property
    def input_muted(self) -> bool:
        return self._input_muted

    async def set_input_muted(self, muted: bool) -> None:
        """Mute or unmute the microphone (docs/architecture/ui.md §5b).

        **This closes the input stream rather than discarding what it produces.** A mute
        that keeps the device open leaves the OS microphone indicator lit and leaves Lumi
        holding audio it has promised not to listen to — and the whole point of the
        control is that the user can believe it.

        The cost is that unmuting has to reopen the device, which can fail (unplugged in
        the meantime). **It fails loudly**: the exception reaches the caller, and the
        state stays muted rather than claiming to listen.
        """
        if self._capture is None or muted == self._input_muted:
            self._input_muted = muted
            return
        if muted:
            if self._vad_worker is not None:
                # **Muted even if the thread will not stop.** The stream closes either
                # way, so nothing more is captured; a worker that outlives this only
                # matters when unmuting, which is where it is refused.
                if not await asyncio.to_thread(self._vad_worker.stop):
                    log.warning("audio.vad_still_running")
                else:
                    self._vad_worker = None
            self._capture.stop()
            self._input_muted = True
            log.info("audio.input_muted")
            return

        if self._vad_worker is not None:
            # ★ **Never a second reader on one ring.** The previous worker did not stop,
            # and two threads pulling from the same ring would each get half the frames —
            # silence that looks like a bad microphone. Staying muted is the honest state.
            log.error("audio.unmute_refused", reason="previous VAD worker is still running")
            raise CaptureUnavailable("The previous VAD worker did not stop")

        self._capture.start()
        await asyncio.to_thread(self._capture.wait_until_open)
        self._start_vad()
        self._input_muted = False
        log.info("audio.input_unmuted")

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

    def _notify(self, notification: VadNotification) -> None:
        """**Called from the VAD thread.** Just hands off to asyncio; nothing is awaited here."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._offer, notification)

    def _offer(self, notification: VadNotification) -> None:
        try:
            self._events.put_nowait(notification)
        except asyncio.QueueFull:
            # **Prioritize the latest.** Drop the old notification to let the current utterance
            # through
            log.warning("audio.event_queue_full")
            with suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
            with suppress(asyncio.QueueFull):
                self._events.put_nowait(notification)

    async def events(self) -> AsyncIterator[VadNotification]:
        """The entry point on the asyncio side. **Corresponds to `_drain_events`
        (docs/architecture/audio.md §3).**
        """
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
