"""Hook — a fixed set. Synchronous, order-guaranteed, can veto.

Single source of definition for the list → docs/contracts/event-model.md "Hook"

**A Hook can observe and refuse, but never rewrite state arbitrarily** (Invariant 6).
Limiting the return value to `Continue` / `Veto` is what implements this; the design
never lets a Hook "return state for Core to apply."

**Growing this set requires an ADR.**
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


#: **Only `before_tool` may veto.** A refusal anywhere else doesn't stop Core
VETOABLE: Final[frozenset[HookName]] = frozenset({HookName.BEFORE_TOOL})


@dataclass(frozen=True, slots=True)
class Continue:
    """OK to proceed."""


@dataclass(frozen=True, slots=True)
class Veto:
    """Stops it. **A reason is required** (never stops silently)."""

    reason: str


HookOutcome = Continue | Veto

#: `payload` is read-only. Never build a path where Core uses something a Hook rewrote
HookHandler = Callable[[Mapping[str, Any]], Awaitable[HookOutcome]]


class HookContractError(RuntimeError):
    """A Hook that can't veto returned `Veto`. **Treated as an implementation bug.**"""


class HookRegistry:
    """Awaited **serially**, in registration order. Never run concurrently (order is guaranteed)."""

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        self._handlers: dict[HookName, list[HookHandler]] = {}

    def register(self, name: HookName, handler: HookHandler) -> None:
        self._handlers.setdefault(name, []).append(handler)

    async def run(self, name: HookName, payload: Mapping[str, Any]) -> HookOutcome:
        """If even one returns `Veto`, **stops right there and never calls the rest.**"""
        for handler in self._handlers.get(name, []):
            outcome = await handler(payload)
            if isinstance(outcome, Veto):
                if name not in VETOABLE:
                    raise HookContractError(f"{name} は veto できない（reason={outcome.reason}）")
                return outcome
        return Continue()
