"""Speaker output. **Instant mute is a hard implementation requirement.**

Design → docs/architecture/audio.md §3, §6 / Decision → ADR-020

> **TTS playback stop is `hard`.** Muting the buffer must silence it instantly
> (docs/contracts/state-machines.md "Cancellation contract").

## How this differs from Phase 0's `play_wav`

Phase 0 only "waited until playback finished" — **there was no way to stop it**.
Phase 1 writes through a ring buffer into an always-open stream, and
**the callback checks `mute_flag` and outputs 0.** This is the barge-in exit point.

## reference ring

Keep **the exact samples written to the playback ring** (not captured from hardware).
Lumi generates the sound it plays itself, so that becomes the reference signal.
**Keep the mute events too** — Phase 2's AEC needs "the waveform that actually reached the speaker."
"""

from __future__ import annotations

import threading
from typing import Any, Final

import numpy as np

from lumi import logging as lumi_logging
from lumi.audio.devices import StreamPlan
from lumi.audio.ring import RingBuffer, Samples

log = lumi_logging.get_logger(__name__)

#: Playback ring length [Provisional]. **Long enough to hold several pre-generated sentences**
PLAYBACK_RING_SECONDS: Final = 30.0
#: Reference signal ring. Sized for AEC delay estimation (Phase 2)
REFERENCE_RING_SECONDS: Final = 4.0


class PlaybackUnavailable(RuntimeError):
    """Cannot open output. **Never fail silently into muted audio.**"""


class SpeakerPlayback:
    """Output stream + ring buffer + `mute_flag`."""

    __slots__ = ("_mute_flag", "_plan", "_reference", "_ring", "_stream", "_underruns")

    def __init__(self, plan: StreamPlan, *, mute_flag: threading.Event | None = None) -> None:
        self._plan = plan
        self._ring = RingBuffer(int(plan.samplerate * PLAYBACK_RING_SECONDS))
        self._reference = RingBuffer(int(plan.samplerate * REFERENCE_RING_SECONDS))
        #: **Shared with the VAD thread.** Once set, output goes silent from the next callback onward
        self._mute_flag = mute_flag or threading.Event()
        self._underruns = 0
        self._stream: Any = None

    @property
    def mute_flag(self) -> threading.Event:
        return self._mute_flag

    @property
    def plan(self) -> StreamPlan:
        return self._plan

    @property
    def reference(self) -> RingBuffer:
        """Read by Phase 2's AEC. **Phase 1 only writes to it.**"""
        return self._reference

    def start(self) -> None:
        import sounddevice as sd

        try:
            self._stream = sd.OutputStream(
                device=self._plan.device.index,
                samplerate=self._plan.samplerate,
                channels=self._plan.channels,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as error:
            raise PlaybackUnavailable(f"出力を開けない: {error}") from error

    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        """**Runs under real-time constraints.** Only touches `mute_flag` and the ring buffer."""
        del time_info
        if status:
            self._underruns += 1

        view = np.asarray(outdata, dtype=np.float32).reshape(-1)
        if self._mute_flag.is_set():
            # * **Sound stops here.** Does not wait for in-progress generation
            view[:] = 0.0
        else:
            self._ring.read_into(view)

        # Keep **what was actually output** as the reference signal (including muted zeros)
        self._reference.write(view)
        del frames

    def write(self, samples: Samples) -> None:
        """Append to the playback queue. **Must be resampled to the device rate before passing in.**"""
        self._ring.write(samples)

    @property
    def queued(self) -> int:
        """Number of samples still queued for playback. **Used by the Inspector and tests to check whether audio was dropped.**"""
        return self._ring.available

    def is_active(self) -> bool:
        """Whether playback is active. **Determines EchoGuard L1's threshold boost.**"""
        return not self._mute_flag.is_set() and self._ring.available > 0

    def mute(self) -> None:
        """**The barge-in exit point.** Synchronous — silence takes effect from the next callback."""
        self._mute_flag.set()

    def unmute(self) -> None:
        self._mute_flag.clear()

    def clear(self) -> None:
        """**Discard queued playback.** Prevents stale audio from resuming after an interruption."""
        self._ring.clear()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._underruns:
            log.warning("playback.underruns", count=self._underruns)
