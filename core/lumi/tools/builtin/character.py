"""L0 の built-in Tool（Class A）。**Phase 1 で登録される唯一のツール。**

> **Phase 1 で L0 しか無くても、`invoke` の経路は本番と同じ。**
> Phase 4a は「Canonicalizer と Policy の中身を書く」だけになる（permission.md §10）。

表情そのものは Phase 1 の Stretch だが、**ツールの経路を貫通させるにはツールが1つ要る。**
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

#: Stage へ意図を送る関数。**Tool は WS も Stage も知らない**（注入される）。
SendExpression = Callable[[ExpressionIntent], Awaitable[None]]

_DEFAULT_INTENSITY: Final = 0.7


@dataclass(frozen=True, slots=True)
class CharacterHandle:
    """`character` lane の Handle。

    fs の fd や input の HWND のような OS 資源を持たない。**持っているのは scope だけ。**
    それでも Handle を介するのは、`BindVerifier` が Kernel 所有であるという
    構造を lane ごとに崩さないため。
    """

    scope: SecurityScope

    def close(self) -> None:
        """解放するものが無い。**空実装であることを明示する。**"""


class SetExpressionTool:
    """`character.set_expression`。**L0**（読み取りでも書き込みでもなく、Lumi 自身の表現）。"""

    name = "character.set_expression"
    description = "Lumi の表情を変える。感情の名前と強さを指定する"
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
        # ユーザーの PC を変えない。Lumi 自身の表現の変更である
        side_effect=SideEffect.NONE,
        # 1回の送信は始めたら止められない。**副作用が none なので L3 制約はかからない**
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
        """**生入力を再解決しない。** 読むのは `handle.scope` だけ（TOCTOU 対策）。"""
        del ctx
        metadata = handle.scope.metadata
        raw_emotion = metadata.get("emotion")
        try:
            emotion = Emotion(raw_emotion)
        except ValueError:
            # **黙って neutral にしない。** 分からない指定は失敗として返す
            return ToolOutcome(
                ok=False,
                error=ToolError(code="unknown_emotion", message=f"未知の emotion: {raw_emotion!r}"),
            )

        intensity = metadata.get("intensity", _DEFAULT_INTENSITY)
        if not isinstance(intensity, int | float) or not 0.0 <= float(intensity) <= 1.0:
            return ToolOutcome(
                ok=False,
                error=ToolError(code="bad_intensity", message=f"範囲外: {intensity!r}"),
            )

        await self._send(ExpressionIntent(emotion=emotion, intensity=float(intensity)))
        return ToolOutcome(ok=True, value={"emotion": emotion.value})
