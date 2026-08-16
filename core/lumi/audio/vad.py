"""VAD — **the barge-in critical path.**

Design → docs/architecture/audio.md §3, §5 / Decision → ADR-003

## Separate mute decisions from speech-segment confirmation

**These two use different thresholds and run separately.**

| | Mute decision | Speech segment confirmation |
|---|---|---|
| Purpose | **Go silent immediately** | Cut the segment to pass to STT |
| Threshold | `mute_threshold` (higher, instant) | `speech_threshold` + hysteresis |
| Min duration | **None** (fires on a single frame) | `min_speech_duration_ms` |
| On false trigger | **Revert and resume playback** | Discard the segment |
| Destination | `mute_flag` (synchronous) | asyncio → Arbiter |

If these were the same, Lumi would keep talking the whole time `min_speech_duration_ms = 250` is waited out.
**Being able to recover from a false trigger lets the mute threshold be pushed much more aggressively.**
"Stop for an instant and resume right away" feels far better than "talk over the user for 300 ms."

## EchoGuard L1 — do not "suppress"

During playback, **only raise** `mute_threshold`. Never stop input.
AIRI's "suppress voice input while speaking" prevents self-loops, but **makes barge-in fundamentally impossible.**
Being interruptible by a loud enough voice is a requirement on Lumi's side.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import numpy as np

from lumi.audio.ring import Samples

#: The rate VAD assumes. Streams can't be opened at this rate, so Core converts internally
SAMPLE_RATE: Final = 16_000
#: Window for Silero v5+ (512 samples = 32 ms)
WINDOW_SAMPLES: Final = 512
#: Concatenate the tail of the previous frame as context (model input is 576)
CONTEXT_SAMPLES: Final = 64
FRAME_MS: Final = WINDOW_SAMPLES * 1000 // SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class VadParams:
    """[Provisional] Starting from AIRI's production values (docs/architecture/audio.md §5)."""

    #: **Mute decision (instant). Tolerates false triggers**
    mute_threshold: float = 0.5
    #: Start of speech segment. **Lower than mute** (so word onsets aren't clipped)
    speech_threshold: float = 0.3
    #: End of speech segment (hysteresis)
    exit_threshold: float = 0.1
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 400
    #: Preroll to avoid cutting off word onsets
    speech_pad_ms: int = 80
    #: **Revert the mute** if no segment is confirmed within this time
    false_trigger_ms: int = 300
    #: **EchoGuard L1.** Multiplier applied to the threshold during playback. **Not suppression**
    playback_boost: float = 1.4

    def mute_threshold_for(self, *, playing: bool) -> float:
        return self.mute_threshold * (self.playback_boost if playing else 1.0)


class VadEvent(StrEnum):
    #: **Go silent now.** The VAD thread sets `mute_flag` synchronously (bypasses the Arbiter)
    MUTE_REQUESTED = "mute_requested"
    #: It was a false trigger. **Resume playback**
    FALSE_TRIGGER = "false_trigger"
    #: Speech segment confirmed → asyncio → `arbiter.interrupt()`
    SPEECH_STARTED = "speech_started"
    #: Speech ended → asyncio → STT
    SPEECH_ENDED = "speech_ended"


class SpeechSegmenter:
    """**Pure logic.** Touches neither the model nor the device (consumes a stream of probabilities, emits events).

    Keeping this pure is what makes barge-in testable.
    """

    __slots__ = (
        "_candidate",
        "_confirmed_since_mute",
        "_muted_frames",
        "_params",
        "_preroll",
        "_segment",
        "_silence_frames",
        "_speech_frames",
        "in_speech",
        "muted",
    )

    def __init__(self, params: VadParams | None = None) -> None:
        self._params = params or VadParams()
        preroll_frames = max(1, self._params.speech_pad_ms // FRAME_MS)
        #: Preroll for word onsets. **Frames from "before" speech begins**
        self._preroll: deque[Samples] = deque(maxlen=preroll_frames)
        #: Frames from when speech starts **until the segment is confirmed**.
        #: Discarding these would drop the entire `min_speech_duration_ms` (250 ms) worth of word onset
        self._candidate: list[Samples] = []
        self._segment: list[Samples] = []
        self._speech_frames = 0
        self._silence_frames = 0
        self._muted_frames = 0
        #: Whether a speech segment was confirmed since this mute. **If confirmed, it wasn't a false trigger**
        self._confirmed_since_mute = False
        self.muted = False
        self.in_speech = False

    @property
    def params(self) -> VadParams:
        return self._params

    def feed(self, probability: float, frame: Samples, *, playing: bool) -> tuple[VadEvent, ...]:
        """Consume one frame's worth and return what happened."""
        events: list[VadEvent] = []
        params = self._params

        # ── (a) Mute decision — instant, **no minimum duration applied** ──
        if playing and not self.muted and probability > params.mute_threshold_for(playing=True):
            self.muted = True
            self._muted_frames = 0
            self._confirmed_since_mute = False
            events.append(VadEvent.MUTE_REQUESTED)
        elif self.muted:
            self._muted_frames += 1

        # ── (b) Speech segment confirmation — hysteresis + minimum duration ──
        if not self.in_speech:
            if probability >= params.speech_threshold:
                # **Buffer even before confirmation.** Discarding would lose the full 250 ms leading up to confirmation
                self._speech_frames += 1
                self._candidate.append(frame)
            else:
                # Not speech. Flush the candidate into preroll and discard it
                self._speech_frames = 0
                self._preroll.extend(self._candidate)
                self._candidate.clear()
                self._preroll.append(frame)

            if self._speech_frames * FRAME_MS >= params.min_speech_duration_ms:
                self.in_speech = True
                self._silence_frames = 0
                # Prepend **preroll (before speech) + candidate (up to confirmation)**
                self._segment = [*self._preroll, *self._candidate]
                self._preroll.clear()
                self._candidate.clear()
                # **This mute was correct.** Exclude it from (c) from now on
                self._confirmed_since_mute = True
                events.append(VadEvent.SPEECH_STARTED)
        else:
            self._segment.append(frame)
            if probability < params.exit_threshold:
                self._silence_frames += 1
            else:
                self._silence_frames = 0
            if self._silence_frames * FRAME_MS >= params.min_silence_duration_ms:
                self.in_speech = False
                self._speech_frames = 0
                events.append(VadEvent.SPEECH_ENDED)

        # ── (c) Recovery from a false trigger ──
        # **Don't call a confirmed utterance a "false trigger."** Once the segment ends, (b) clears
        # in_speech, so without `_confirmed_since_mute`, this would always fire right after a
        # correctly interrupted utterance. The caller would then interpret that as "the
        # interruption was a mistake" and **resume playback that should have stayed interrupted.**
        if (
            self.muted
            and not self.in_speech
            and not self._confirmed_since_mute
            and self._muted_frames * FRAME_MS >= params.false_trigger_ms
        ):
            self.muted = False
            self._muted_frames = 0
            events.append(VadEvent.FALSE_TRIGGER)

        return tuple(events)

    def take(self) -> Samples:
        """Retrieve the confirmed segment. **Empties it once retrieved.**"""
        if not self._segment:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._segment).astype(np.float32)
        self._segment = []
        return audio

    def unmute(self) -> None:
        """Clear the mute state from outside. **The only way to revert it after a confirmed utterance.**

        (c) only handles false triggers, so **the caller must explicitly revert it
        between interrupting and the next time it speaks** (`VadWorker.resume` → `AudioIO.resume_listening`).
        Failing to call this leaves `muted` set, so **barge-in only works once.**
        """
        self.muted = False
        self._muted_frames = 0
        self._confirmed_since_mute = False


class VadModelUnavailable(RuntimeError):
    """Model not found / unreadable. **Never silently treat this as "no voice."**"""


def default_model_path() -> Path:
    """**Uses the Silero VAD bundled with faster-whisper** (→ docs/licensing.md §4.6).

    We don't fetch it separately because PyPI's `silero-vad` **depends on torch** (R1).

    **What this does not guarantee**: this depends on faster-whisper's internal package
    structure, and will break if an update moves the path. **It fails explicitly when
    broken** (fail-closed), so VAD never silently stops working.
    """
    try:
        import faster_whisper
    except ImportError as error:  # pragma: no cover
        raise VadModelUnavailable(f"faster_whisper が無い: {error}") from error

    path = Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"
    if not path.is_file():
        raise VadModelUnavailable(f"Silero VAD の ONNX が見つからない: {path}")
    return path


class SileroVad:
    """Silero VAD (ONNX Runtime, CPU). **Uses no VRAM.**

    **Never call from the audio callback.** ONNX inference holds the GIL, and
    allocator/thread-pool behavior can produce spikes of tens of milliseconds.
    That hits p99 directly and can break even normal playback (docs/architecture/audio.md §3).
    """

    __slots__ = ("_c", "_context", "_h", "_session")

    def __init__(self, model_path: Path | None = None) -> None:
        import onnxruntime  # Lazy import; keeps startup light

        options = onnxruntime.SessionOptions()
        # **Pin to 1 CPU thread.** VAD does small inferences every 32 ms;
        # adding threads would only compete with LLM / TTS for CPU without speeding anything up
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        try:
            self._session = onnxruntime.InferenceSession(
                str(model_path or default_model_path()),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise VadModelUnavailable(str(error)) from error

        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def probability(self, frame: Samples) -> float:
        """Consume 512 samples (32 ms / 16 kHz) and return the speech probability."""
        if len(frame) != WINDOW_SAMPLES:
            raise ValueError(f"{WINDOW_SAMPLES} サンプルが必要（来たのは {len(frame)}）")

        # From v5 onward, input is "the previous frame's trailing 64 samples + this frame's 512"
        window = np.concatenate((self._context, frame)).reshape(1, -1).astype(np.float32)
        outputs: list[Any] = self._session.run(None, {"input": window, "h": self._h, "c": self._c})
        probability, self._h, self._c = outputs[0], outputs[1], outputs[2]
        self._context = frame[-CONTEXT_SAMPLES:].astype(np.float32)
        return float(np.asarray(probability).reshape(-1)[0])

    def reset(self) -> None:
        """Discard state across sessions. **Leftover LSTM state causes misjudgments.**"""
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
