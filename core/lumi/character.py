"""The "intent" passed to the character. **Not a parameter set.**

Type definitions → docs/interfaces/renderer.md / Decision → ADR-009

VRM and Live2D have fundamentally different expression models
(blending named blend shapes / directly manipulating raw parameters).
**If Core knew blend shape names, that would be meaningless for Live2D.**

> **The Renderer never takes part in blending.** It expresses the intent it receives
> as-is, and **falls back on its own** if it can't. Core never branches on `capabilities()`.

**`Emotion` is defined only here** (docs/interfaces/renderer.md test 1).
Never redefined per Renderer. Since it's a value that goes on the wire, it's also
cross-checked against `docs/contracts/wire.json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: Sent to the Stage when the expression changes. **A notify, not a command** — Core never
#: waits to be told a face finished changing (docs/contracts/wire.json)
METHOD_EXPRESSION: Final = "stage.character.expression"


class Emotion(StrEnum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    THINK = "think"
    CURIOUS = "curious"
    AWKWARD = "awkward"
    SLEEPY = "sleepy"


@dataclass(frozen=True, slots=True)
class ExpressionIntent:
    """`intensity` is communicated as 0.0-1.0.

    **Mapping it onto an actual value range is the Renderer's implementation detail.**
    VRM's blend shapes accept up to 1.0, but capping around 0.7-0.8 looks more natural.
    That's a rendering detail, not something Core needs to know.
    """

    emotion: Emotion
    intensity: float = 0.7
    blend_ms: int = 200
    #: `None` keeps it as-is. If given, it reverts automatically
    duration_ms: int | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "emotion": self.emotion.value,
            "intensity": self.intensity,
            "blend_ms": self.blend_ms,
            "duration_ms": self.duration_ms,
        }
