"""TTSProvider's contract.

Type definitions → docs/interfaces/provider.md "TTSProvider"

## `synthesize` returns more than just audio [finalized during Phase 1 implementation]

The docs' original contract was `-> AudioBuffer`, but **the lip-sync timeline can only
be built "after" synthesis** (AivisSpeech doesn't return phoneme lengths from
`audio_query` → docs/interfaces/renderer.md). The audio and the mouth timeline are
determined together, so **they're returned as a single result.**

When `timeline` is `None`, **no visemes are sent** (mouth stays closed).
Better to not move the mouth than move it on bogus timing.

## What being a separate CPU process means

**It lets the LLM claim all the VRAM.** That's the entire reason TTS was chosen this
way (ADR-008). `resource_hint()`'s `EXTERNAL_PROCESS` is that declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import Provider
from lumi.providers.tts.viseme import VisemeTimeline


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    """Synthesis result. WAV bytes plus the mouth timeline."""

    wav: bytes
    #: `None` = mouth doesn't move (no phoneme info was available)
    timeline: VisemeTimeline | None


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """Which voice to speak with. **Selecting it is the Content Pack's job** (`voice.toml`)."""

    speaker: int
    name: str = ""


class TTSProvider(Provider, Protocol):
    async def synthesize(
        self, text: str, voice: VoiceConfig, cancel_token: CancelToken
    ) -> SpeechAudio: ...

    def supported_languages(self) -> frozenset[str]:
        """**Fails explicitly for an unsupported language.** Never silently falls back to reading it
        in English.
        """
        ...
