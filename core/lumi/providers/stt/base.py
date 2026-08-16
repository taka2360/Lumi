"""STTProvider's contract.

Type definitions → docs/interfaces/provider.md "STTProvider"

**The common audio representation is 16 kHz mono float32.** Both VAD and STT receive
audio in this form. Streams are opened at the device's default rate (usually 48 kHz),
and **Core resamples to 16 kHz** before passing it here (docs/architecture/audio.md §8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import Provider

#: 16 kHz mono float32. **Defined in exactly one place** (so VAD and STT never disagree)
AudioBuffer = npt.NDArray[np.float32]

#: Sample rate assumed by VAD / STT
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
