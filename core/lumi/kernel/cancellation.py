"""The Cancellation contract — three kinds.

Single source of definition → docs/contracts/state-machines.md

> **The assumption that `cancel_token.fire()` stops everything is wrong.**

subprocesses, HTTP requests, GPU inference, and OS input injection each differ in both
their cancellation mechanism and whether they can even be interrupted at all. **Each
declares how it stops.**
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum


class Cancellation(StrEnum):
    """**Every Tool / unit of work must declare exactly one of these three.**"""

    #: Periodically checks cancel_token and safely stops at the next checkpoint.
    #: e.g. LLM streams, loop processing, file scanning
    COOPERATIVE = "cooperative"
    #: Can be forcibly killed from outside. e.g. subprocess, HTTP, **stopping TTS playback**
    HARD = "hard"
    #: Once started, cannot be stopped until completion. e.g. a single GPU inference,
    #: one OS input event
    #: **Anything with a side effect is pinned to risk >= L3** (verified at
    #: registration. permission.md §3.1)
    NON_CANCELLABLE = "non_cancellable"


class CancelToken:
    """Only conveys "please stop." **Does not guarantee that anything actually stops.**

    The only guarantee is that "firing can be observed" — whether it actually stops
    depends on the receiver's contract (`Cancellation`).
    Conflating these two leads to mishandling `non_cancellable`.
    """

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    def fire(self, reason: str) -> None:
        """**Idempotent.** Subsequent calls are ignored, and **the first reason is what's kept.**

        Overwriting it later would paint over "why it stopped" with only the last one.
        When investigating barge-in, it's the first reason that matters.
        """
        if self._event.is_set():
            return
        self._reason = reason
        self._event.set()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait(self) -> str:
        """Waits until fired, and returns the reason."""
        await self._event.wait()
        return self._reason or ""


class CancellationContractError(ValueError):
    """The contract and the implementation disagree. **Fails at startup or registration** (fail-closed)."""


@dataclass(frozen=True, slots=True)
class Cancellable:
    """A "stoppable unit of work" hanging off an Activity.

    Tool execution, LLM streams, and TTS generation are all seen by the Arbiter as
    this type. **The kernel knows nothing about Tools** (dependency direction,
    docs/architecture/core.md §4), so all it knows is "the contract for how to stop."
    """

    id: str
    #: The name shown in the Inspector and `InterruptResult`. Human-readable
    label: str
    contract: Cancellation
    #: The stop mechanism for `cooperative`
    cancel_token: CancelToken = field(default_factory=CancelToken)
    #: The stop mechanism for `hard`. **Required if `hard`**
    kill: Callable[[], Awaitable[None]] | None = None
    #: Set by the work itself upon completion (whether normal or interrupted).
    #: **Without this, the Arbiter has no way to know "did it stop," and the grace
    #: period becomes meaningless.**
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if self.contract is Cancellation.HARD and self.kill is None:
            # Declaring `hard` without a way to actually stop it is the same as barge-in not working.
            raise CancellationContractError(f"{self.label}: hard を宣言したが kill が無い")

    def mark_finished(self) -> None:
        """Called by the work itself in `finally`. **Called even on normal completion** (the Arbiter doesn't distinguish)."""
        self.finished.set()
