"""Command — "execute this." **Something that needs a result.**

Contract → docs/contracts/event-model.md

```
Does it need a result? → Yes: Command
                       → No ↓
Did it come from outside? → Yes: Signal
                          → No : DomainEvent (published by Core)
```

**When unsure, use Command.** Relaxing it into an Event later is easy; converting
control flow already built on Events back into a Command is hard.

AIRI choreographs module lifecycles with roughly 60 kinds of WS events, and "where did
it get stuck" has become untraceable. **Lumi makes lifecycles an explicit sequence of Commands.**
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from lumi.kernel.ids import CommandId, CorrelationId


@dataclass(frozen=True, slots=True)
class Command:
    """Dispatched to a single handler. **Has a return value, and can fail.**"""

    id: CommandId
    type: str
    payload: Mapping[str, Any]
    correlation_id: CorrelationId
    #: **Required** for any Command with a side effect (docs/architecture/recovery.md §3)
    idempotency_key: str | None = None


class CommandError(RuntimeError):
    """A failure related to dispatching a Command."""


class UnknownCommand(CommandError):
    """No handler is registered. **Never guessed at.**"""


class DuplicateHandler(CommandError):
    """A second handler was registered for the same type. **Breaks "single handler."**"""


class MissingIdempotencyKey(CommandError):
    """A Command with a side effect is missing `idempotency_key`.

    Without it, "was this the same operation" can't be determined after a crash, and
    Crash Recovery (Phase 4a) can't work. **This is closed off starting in Phase 1.**
    """


CommandHandler = Callable[[Command], Awaitable[Any]]


class CommandBus:
    """A single handler per type.

    **Holds no queue.** Since the caller `await`s it, backpressure applies naturally
    as "the caller waits." Inserting a queue would conflict with a Command's defining
    property: "it needs a result."
    """

    __slots__ = ("_handlers", "_side_effecting")

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}
        self._side_effecting: set[str] = set()

    def register(
        self, command_type: str, handler: CommandHandler, *, has_side_effect: bool = False
    ) -> None:
        """A type with `has_side_effect=True` is never dispatched without `idempotency_key`."""
        if command_type in self._handlers:
            raise DuplicateHandler(command_type)
        self._handlers[command_type] = handler
        if has_side_effect:
            self._side_effecting.add(command_type)

    async def dispatch(self, command: Command) -> Any:
        handler = self._handlers.get(command.type)
        if handler is None:
            raise UnknownCommand(command.type)
        if command.type in self._side_effecting and command.idempotency_key is None:
            raise MissingIdempotencyKey(command.type)
        return await handler(command)
