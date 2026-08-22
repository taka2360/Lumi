"""Reads WAV data. **Pure functions** (never touches a device, never touches a file).

Never trust the engine's output (Invariant 3). Fails explicitly if malformed.

**There is no encoder here.** One existed to write captured microphone audio out for
debugging; docs/contracts/privacy.md §6 forbids that path, in either direction, so the
only thing this module does is decode what a TTS engine hands back.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass


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
