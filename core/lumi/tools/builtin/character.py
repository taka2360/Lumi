"""An L0 built-in Tool (Class A). **The only tool registered in Phase 1.**

> **Even with only L0 in Phase 1, the `invoke` path is identical to production.**
> Phase 4a just becomes "write the contents of the Canonicalizer and Policy"
> (permission.md §10).

The expression feature itself is a Phase 1 stretch goal, but **one tool is needed to
exercise the Tool path end to end.**
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from lumi.character import Emotion, ExpressionIntent
from lumi.kernel.cancellation import Cancellation
from lumi.permission.policy import PermissionSpec, Risk, SideEffect
from lumi.permission.scope import ScopeLane, SecurityScope
from lumi.tools.base import ToolContext, ToolError, ToolKind, ToolOutcome

# : The function that sends intent to the Stage. **The Tool knows nothing about WS or the Stage**
# (it's injected).
SendExpression = Callable[[ExpressionIntent], Awaitable[None]]

_DEFAULT_INTENSITY: Final = 0.7


@dataclass(frozen=True, slots=True)
class CharacterHandle:
    """The Handle for the `character` lane.

    Holds no OS resource like `fs`'s fd or `input`'s HWND. **All it holds is a scope.**
    It still goes through a Handle so the structure — "`BindVerifier` is owned by the
    Kernel" — stays consistent lane by lane.
    """

    scope: SecurityScope

    def close(self) -> None:
        """Nothing to release. **Explicitly an empty implementation.**"""


class SetExpressionTool:
    """`character.set_expression`. **L0** (neither a read nor a write — Lumi's own expression)."""

    name = "character.set_expression"
    description = "Change Lumi's facial expression. Specify the emotion and intensity"
    input_schema: Mapping[str, Any] = {
        "type": "object",
        "properties": {
            "emotion": {"type": "string", "enum": [e.value for e in Emotion]},
            "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["emotion"],
    }
    output_schema: Mapping[str, Any] = {"type": "object", "properties": {}}

    lane = ScopeLane.CHARACTER
    kind = ToolKind.CONTROL
    permission = PermissionSpec(
        capability="character.expression",
        risk=Risk.L0,
        reversible=True,
        # Doesn't change anything on the user's PC. This changes Lumi's own expression
        side_effect=SideEffect.NONE,
        # A single send can't be stopped once started. **No L3 constraint applies since the side
        # effect is none**
        cancellation=Cancellation.NON_CANCELLABLE,
    )
    concurrency_safe = True
    idempotent = True
    deferred = False

    __slots__ = ("_send",)

    def __init__(self, send: SendExpression) -> None:
        self._send = send

    def bind(self, ctx: ToolContext, scope: Any) -> CharacterHandle:
        del ctx
        return CharacterHandle(scope=scope)

    async def execute(self, ctx: ToolContext, handle: Any) -> ToolOutcome:
        """**Never re-resolves raw input.** Only reads `handle.scope` (defends against TOCTOU)."""
        del ctx
        metadata = handle.scope.metadata
        raw_emotion = metadata.get("emotion")
        try:
            emotion = Emotion(raw_emotion)
        except ValueError:
            # **Never silently falls back to neutral.** An unrecognized value is returned as a
            # failure
            return ToolOutcome(
                ok=False,
                error=ToolError(
                    code="unknown_emotion", message=f"Unknown emotion: {raw_emotion!r}"
                ),
            )

        intensity = metadata.get("intensity", _DEFAULT_INTENSITY)
        if not isinstance(intensity, int | float) or not 0.0 <= float(intensity) <= 1.0:
            return ToolOutcome(
                ok=False,
                error=ToolError(code="bad_intensity", message=f"Out of range: {intensity!r}"),
            )

        await self._send(ExpressionIntent(emotion=emotion, intensity=float(intensity)))
        return ToolOutcome(ok=True, value={"emotion": emotion.value})
