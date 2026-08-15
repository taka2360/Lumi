"""Hook — 固定セット。同期的・順序保証あり・veto 可能。

一覧の唯一の定義場所 → docs/contracts/event-model.md「Hook」

**Hook は観測と拒否はできるが、任意の状態書き換えはできない**（Invariant 6）。
戻り値を `Continue` / `Veto` に限っているのがその実装であり、
「状態を返して Core がそれを適用する」設計にはしない。

**このセットを増やすには ADR を要求する。**
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final


class HookName(StrEnum):
    BEFORE_LLM = "before_llm"
    AFTER_LLM = "after_llm"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    BEFORE_SPEAK = "before_speak"
    AFTER_SPEAK = "after_speak"
    ON_MEMORY_WRITE = "on_memory_write"
    ON_ACTIVITY_START = "on_activity_start"
    ON_ACTIVITY_END = "on_activity_end"
    ON_APP_START = "on_app_start"
    ON_APP_SHUTDOWN = "on_app_shutdown"


#: **veto してよいのは `before_tool` だけ。** 他で拒否されても Core は止まらない
VETOABLE: Final[frozenset[HookName]] = frozenset({HookName.BEFORE_TOOL})


@dataclass(frozen=True, slots=True)
class Continue:
    """通してよい。"""


@dataclass(frozen=True, slots=True)
class Veto:
    """止める。**理由は必須**（黙って止めない）。"""

    reason: str


HookOutcome = Continue | Veto

#: payload は読み取り専用。Hook が書き換えたものを Core が使う経路を作らない
HookHandler = Callable[[Mapping[str, Any]], Awaitable[HookOutcome]]


class HookContractError(RuntimeError):
    """veto できない Hook が `Veto` を返した。**実装ミスとして落とす。**"""


class HookRegistry:
    """登録順に、**直列で** await する。並行実行しない（順序保証があるため）。"""

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        self._handlers: dict[HookName, list[HookHandler]] = {}

    def register(self, name: HookName, handler: HookHandler) -> None:
        self._handlers.setdefault(name, []).append(handler)

    async def run(self, name: HookName, payload: Mapping[str, Any]) -> HookOutcome:
        """1つでも `Veto` を返したら、**そこで止めて後続を呼ばない。**"""
        for handler in self._handlers.get(name, []):
            outcome = await handler(payload)
            if isinstance(outcome, Veto):
                if name not in VETOABLE:
                    raise HookContractError(f"{name} は veto できない（reason={outcome.reason}）")
                return outcome
        return Continue()
