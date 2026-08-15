"""WAV を読む。**純粋関数**（デバイスに触らない）。

エンジンの出力を信用しない（Invariant 3）。壊れていたら明示的に失敗する。
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass


class WavError(ValueError):
    """WAV として読めなかった。"""


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
