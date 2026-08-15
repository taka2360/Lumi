"""STTProvider の契約。

型の定義 → docs/interfaces/provider.md「STTProvider」

**音声の共通表現は 16 kHz mono float32。** VAD も STT もこの形で受け取る。
ストリームはデバイス既定のレートで開き（48 kHz が普通）、
**Core 内で 16 kHz にリサンプルしてから**ここに渡す（docs/architecture/audio.md §8）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import Provider

#: 16 kHz mono float32。**この型を1箇所で決めておく**（VAD と STT で食い違わないように）
AudioBuffer = npt.NDArray[np.float32]

#: VAD / STT が前提とするサンプルレート
SAMPLE_RATE = 16_000


@dataclass(frozen=True, slots=True)
class Segment:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    language: str
    confidence: float | None = None
    segments: tuple[Segment, ...] = ()


class STTProvider(Provider, Protocol):
    async def transcribe(
        self, audio: AudioBuffer, language: str | None, cancel_token: CancelToken
    ) -> Transcription: ...
