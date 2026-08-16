"""Microphone input and the VAD thread.

Design → docs/architecture/audio.md §3

```
[audio callback]  Only writes to the ring        no inference, no allocation, no locking
[VAD thread]      read → resample → infer → decide  * sound stops here
[asyncio]         Activity arbitration / STT       can be slow
```

**"Sound stopping" is done synchronously by the VAD thread and bypasses the Arbiter.**
It's not an Activity state transition, so it's out of scope for Invariant 4
(docs/contracts/state-machines.md / ADR-018).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Final

import numpy as np

from lumi import logging as lumi_logging
from lumi.audio.devices import StreamPlan
from lumi.audio.resample import resample, to_mono
from lumi.audio.ring import RingBuffer, Samples
from lumi.audio.vad import (
    FRAME_MS,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    SileroVad,
    SpeechSegmenter,
    VadEvent,
    VadParams,
)

log = lumi_logging.get_logger(__name__)

# : Input ring length [Provisional]. **Kept long rather than risk overflow** (so a temporary VAD
# delay doesn't lose data)
CAPTURE_RING_SECONDS: Final = 8.0

#: Grace period before the first frame arrives. **A successful `open()` doesn't mean "listening."**
#: (A disabled endpoint can open but never deliver frames → observed in Phase 0)
FIRST_FRAME_TIMEOUT_S: Final = 3.0

#: How long the VAD thread sleeps when the ring is empty. Well under the frame length (32 ms)
_IDLE_SLEEP_S: Final = 0.004

# : Cap on speech segment length [Provisional]. **Never buffer indefinitely** (continuous ambient
# noise would eat RAM)
MAX_SEGMENT_SECONDS: Final = 30.0


class CaptureUnavailable(RuntimeError):
    """Fails to open / never receives a frame. **Never end up "thinking it's listening while hearing
    nothing."**
    """


#: Notification from VAD. `audio` is only populated for SPEECH_ENDED; the `float` is the
#: `perf_counter` timestamp of when the event's audio actually happened (docs/architecture/audio.md
#: §7). **Passing "now" instead would hide the ring backlog from every latency measurement.**
VadListener = Callable[[VadEvent, Samples | None, float], None]


class MicrophoneCapture:
    """Input stream. **The callback only writes to the ring.**"""

    __slots__ = ("_first_frame", "_overflows", "_plan", "_ring", "_stream")

    def __init__(self, plan: StreamPlan) -> None:
        self._plan = plan
        capacity = int(plan.samplerate * CAPTURE_RING_SECONDS)
        self._ring = RingBuffer(capacity)
        self._first_frame = threading.Event()
        #: Overflow count reported by PortAudio. **The callback only increments it**
        #: (log I/O would violate the deadline). Read by the VAD thread and at stop time
        self._overflows = 0
        self._stream: Any = None

    @property
    def ring(self) -> RingBuffer:
        return self._ring

    @property
    def plan(self) -> StreamPlan:
        return self._plan

    def start(self) -> None:
        import sounddevice as sd

        try:
            self._stream = sd.InputStream(
                device=self._plan.device.index,
                samplerate=self._plan.samplerate,
                channels=self._plan.channels,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as error:
            raise CaptureUnavailable(f"入力を開けない: {error}") from error

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        """**Runs under real-time constraints.** No inference, no allocation, no locking.

        `to_mono` only allocates for multi-channel input. For single channel (the default),
        a `reshape` view is enough.
        """
        del frames, time_info
        if status:
            # Overflow, etc. **Not swallowed, but no log write happens here** (I/O would violate the
            # deadline)
            self._overflows += 1
        samples = np.asarray(indata, dtype=np.float32).reshape(-1)
        if self._plan.channels > 1:
            samples = to_mono(samples, self._plan.channels)
        self._ring.write(samples)
        if not self._first_frame.is_set():
            self._first_frame.set()

    def wait_until_open(self, timeout: float = FIRST_FRAME_TIMEOUT_S) -> None:
        """**Only counted as "listening" once the first frame actually arrives.**

        `open()` succeeds even on a disabled endpoint (observed in Phase 0).
        If nothing arrives, **fail explicitly.**
        """
        if not self._first_frame.wait(timeout):
            raise CaptureUnavailable(
                f"{self._plan.describe()}: {timeout} 秒待ってもフレームが来ない"
            )

    @property
    def overflows(self) -> int:
        return self._overflows

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._overflows or self._ring.dropped:
            # **Always check for silent degradation when stopping**
            log.warning("capture.lossy", overflows=self._overflows, dropped=self._ring.dropped)


class VadWorker:
    """**A dedicated OS thread.** Never goes through asyncio.

    This is what handles "go silent immediately." All that gets handed to asyncio is the
    subsequent, **can-be-slow work**: "stop the Activity," "run STT."

    ## Two paths revert the mute

    | Path | When | Who |
    |---|---|---|
    | Recovery from a false trigger | after `false_trigger_ms` elapses | VAD thread (automatic) |
    | **`resume()`** | right before speaking again, post-utterance | **the asyncio side** |

    Without the latter, **barge-in only works once.** A mute from a confirmed segment
    isn't a false trigger, so it doesn't revert automatically, and the next
    `MUTE_REQUESTED` won't fire while `muted` stays set (path (a) of `SpeechSegmenter.feed`).
    """

    __slots__ = (
        "_is_playing",
        "_listener",
        "_mute_flag",
        "_pending",
        "_resume",
        "_ring",
        "_segmenter",
        "_source_rate",
        "_stop",
        "_thread",
        "_vad",
    )

    def __init__(
        self,
        ring: RingBuffer,
        source_rate: int,
        mute_flag: threading.Event,
        listener: VadListener,
        *,
        vad: SileroVad | None = None,
        params: VadParams | None = None,
        is_playing: Callable[[], bool] | None = None,
    ) -> None:
        self._ring = ring
        self._source_rate = source_rate
        self._mute_flag = mute_flag
        self._listener = listener
        self._vad = vad
        self._segmenter = SpeechSegmenter(params)
        self._is_playing = is_playing or (lambda: False)
        self._pending: Samples = np.zeros(0, dtype=np.float32)
        self._stop = threading.Event()
        #: The asyncio side's "OK to revert the mute" signal. **Passed via an Event**
        #: (never touch `SpeechSegmenter` directly from another thread)
        self._resume = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._vad is None:
            self._vad = SileroVad()
        self._thread = threading.Thread(target=self._loop, name="lumi-vad", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        # Read at the device rate, in chunks equal to one 16 kHz window's worth
        chunk = max(1, WINDOW_SAMPLES * self._source_rate // SAMPLE_RATE)
        while not self._stop.is_set():
            raw = self._ring.read(chunk)
            if raw is None:
                time.sleep(_IDLE_SLEEP_S)
                continue
            read_at = time.perf_counter()
            # Everything still in the ring is *newer* than what was just read, so the chunk's
            # audio ended this far in the past. **Without this the backlog is invisible** and
            # every latency would be reported as if the VAD were always caught up.
            audio_at = read_at - self._ring.available / self._source_rate
            converted = resample(raw, self._source_rate, SAMPLE_RATE)
            self._pending = (
                converted if len(self._pending) == 0 else np.concatenate((self._pending, converted))
            )
            while len(self._pending) >= WINDOW_SAMPLES:
                window = self._pending[:WINDOW_SAMPLES]
                self._pending = self._pending[WINDOW_SAMPLES:]
                self._process(window, read_at, audio_at)

    def resume(self) -> None:
        """**Resets to a state that can accept the next barge-in.** Called from the asyncio side.

        Only sets the `Event`; the actual clearing is done by the VAD thread.
        This keeps `SpeechSegmenter`'s state from being written by two threads.
        """
        self._resume.set()

    def _process(self, window: Samples, read_at: float, audio_at: float) -> None:
        """`audio_at` is when this window's audio actually happened, in `perf_counter` terms.

        It is an approximation: one ring read may yield several windows, and they all get the
        same timestamp. The error is bounded by one read (~32 ms) and **biased toward reporting
        latency as larger than it was**, which is the safe direction for an SLO.
        """
        assert self._vad is not None
        if self._resume.is_set():
            self._resume.clear()
            self._segmenter.unmute()
            self._mute_flag.clear()

        probability = self._vad.probability(window)
        events = self._segmenter.feed(probability, window, playing=self._is_playing())

        for event in events:
            if event is VadEvent.MUTE_REQUESTED:
                # * **critical path.** Sound stops here (synchronous, bypasses the Arbiter)
                self._mute_flag.set()
                muted_at = time.perf_counter()
                speech_at = audio_at - FRAME_MS / 1000.0
                log.info(
                    "vad.mute",
                    # What the implementation controls: read → silent (target < 50 ms)
                    mute_latency_ms=round((muted_at - read_at) * 1000, 2),
                    # What the user feels: speech onset → silent (target < 120 ms).
                    # **Measured separately** — the frame boundary and the ring backlog are in
                    # here, and no amount of faster inference removes them
                    perceived_ms=round((muted_at - speech_at) * 1000, 2),
                    probability=round(probability, 3),
                )
                self._listener(event, None, speech_at)
            elif event is VadEvent.FALSE_TRIGGER:
                # **It was a false trigger. Revert.** This is what lets the mute threshold be pushed
                # aggressively
                self._mute_flag.clear()
                log.info("vad.false_trigger")
                self._listener(event, None, audio_at)
            elif event is VadEvent.SPEECH_ENDED:
                audio = self._segmenter.take()
                # The user actually stopped talking `min_silence_duration_ms` ago — that is
                # where the turn's clock starts, **not when Core noticed** (audio.md §7)
                ended_at = audio_at - self._segmenter.params.min_silence_duration_ms / 1000.0
                self._listener(event, _clamp(audio), ended_at)
            else:
                self._listener(event, None, audio_at)


def _clamp(audio: Samples) -> Samples:
    """**Never pass an unboundedly long segment to STT.** Truncate, keeping the tail."""
    limit = int(SAMPLE_RATE * MAX_SEGMENT_SECONDS)
    if len(audio) <= limit:
        return audio
    log.warning("vad.segment_truncated", samples=len(audio), limit=limit)
    return audio[-limit:]
