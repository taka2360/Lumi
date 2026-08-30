"""Which speaker says the reply, and how loudly. **Core hardcodes no default.**

Decision → docs/decisions/ADR-032-tts-speed.md / docs/decisions/ADR-046-tts-volume.md

Two owners meet here. The **Content Pack** decides who Lumi sounds like — the speaker and
the pack's own volume — and the **user** owns the sliders, which are multipliers over it
rather than replacements for it. A pack's voice therefore survives someone turning Lumi
down and back up.

## The engine's default is asked for, not assumed

Which voices exist depends on the machine: AivisSpeech fetches its models at runtime. A
default written into Core would name a speaker that is not installed.

**A Provider that has no default is not a broken Provider**, so this asks and copes,
rather than the `TTSProvider` protocol requiring an answer every implementation must
have. Narrowing the protocol to remove one cast would refuse Providers that work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

from lumi import logging as lumi_logging
from lumi.content.pack import CharacterPack
from lumi.providers.base import ProviderError
from lumi.providers.tts.base import VOLUME_SCALE_MAX, TTSProvider, VoiceConfig
from lumi.settings import (
    TTS_SPEED_MAX,
    TTS_SPEED_MIN,
    TTS_VOLUME_MAX,
    TTS_VOLUME_MIN,
)

log = lumi_logging.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VoiceScales:
    """The speed and volume **as they were when the turn started** (ADR-032 / ADR-046).

    A turn keeps what it began with: a slider moved while Lumi is mid-sentence changes the
    next reply, never the one already being spoken.
    """

    speed: float
    volume: float


def validate_speed(speed: float) -> float:
    """Keeps the runtime API aligned with the Core-owned settings contract."""
    if not math.isfinite(speed) or not TTS_SPEED_MIN <= speed <= TTS_SPEED_MAX:
        raise ValueError(
            f"tts_speed must be finite and between {TTS_SPEED_MIN} and {TTS_SPEED_MAX}"
        )
    return speed


def validate_volume(volume: float) -> float:
    """Same contract for the volume multiplier (ADR-046).

    **A multiplier, not a level.** `1.0` leaves the Content Pack's own volume alone.
    """
    if not math.isfinite(volume) or not TTS_VOLUME_MIN <= volume <= TTS_VOLUME_MAX:
        raise ValueError(
            f"tts_volume must be finite and between {TTS_VOLUME_MIN} and {TTS_VOLUME_MAX}"
        )
    return volume


class VoiceResolver:
    """Turns a pack and two sliders into the `VoiceConfig` for one turn."""

    __slots__ = ("_pack",)

    def __init__(self, pack: CharacterPack) -> None:
        self._pack = pack

    def resolve(self, tts: TTSProvider, scales: VoiceScales) -> VoiceConfig:
        """If the Content Pack doesn't specify a speaker, **defer to the engine's default.**

        Core doesn't hardcode a default because **which models are installed varies by
        environment** (AivisSpeech fetches models at runtime).
        """
        speaker = self._pack.voice.speaker
        volume = self._volume(scales.volume)
        if speaker is not None:
            return VoiceConfig(
                speaker=speaker,
                name=self._pack.voice.credit.name,
                volume_scale=volume,
                speed_scale=scales.speed,
            )
        default = getattr(tts, "default_voice", None)
        if default is None:
            raise ProviderError("no_voice", "Cannot determine speaker")
        voice = cast("VoiceConfig", default())
        return VoiceConfig(
            speaker=voice.speaker,
            name=voice.name,
            volume_scale=volume,
            speed_scale=scales.speed,
        )

    def _volume(self, multiplier: float) -> float:
        """The Content Pack's volume, scaled by the Core-owned setting (ADR-046).

        **Clamped to what the engine accepts, and said out loud when it is.** A pack loud
        enough to run past the ceiling would otherwise let the slider move with nothing
        happening — which is indistinguishable from a broken control.
        """
        scaled = self._pack.voice.volume * multiplier
        if scaled > VOLUME_SCALE_MAX:
            log.info(
                "reactive.volume_clamped",
                requested=scaled,
                maximum=VOLUME_SCALE_MAX,
                pack_volume=self._pack.voice.volume,
            )
            return VOLUME_SCALE_MAX
        return scaled
