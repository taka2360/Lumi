"""Reads and writes WAV data. **Pure functions** (never touches a device).

Never trust the engine's output (Invariant 3). Fails explicitly if malformed.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

from lumi.audio.ring import Samples


class WavError(ValueError):
    """Couldn't be read as WAV."""


@dataclass(frozen=True, slots=True)
class Wav:
    frames: bytes
    channels: int
    sample_width: int
    sample_rate: int

    @property
    def duration_seconds(self) -> float:
        divisor = self.sample_rate * self.channels * self.sample_width
        return len(self.frames) / divisor if divisor else 0.0


def decode_wav(data: bytes) -> Wav:
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            return Wav(
                frames=source.readframes(source.getnframes()),
                channels=source.getnchannels(),
                sample_width=source.getsampwidth(),
                sample_rate=source.getframerate(),
            )
    except (wave.Error, EOFError) as error:
        raise WavError(str(error)) from error


def encode_wav(mono: Samples, sample_rate: int) -> bytes:
    """Serialize a mono float32 waveform as 16-bit PCM WAV.

    **For listening to what Core actually heard** (`lumi.audio.dump`). Values are clipped
    rather than normalized: a scaled dump would hide clipping, which is one of the things
    the dump exists to reveal.
    """
    clipped = np.clip(mono.astype(np.float32, copy=False), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(sample_rate)
        sink.writeframes(pcm)
    return buffer.getvalue()
