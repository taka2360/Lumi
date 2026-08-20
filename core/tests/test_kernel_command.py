"""Command and Hook. **docs/contracts/event-model.md "Command" "Hook".**"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from lumi.kernel.command import (
    Command,
    CommandBus,
    DuplicateHandler,
    MissingIdempotencyKey,
    UnknownCommand,
)
from lumi.kernel.hooks import (
    Continue,
    HookContractError,
    HookName,
    HookRegistry,
    Veto,
)
from lumi.kernel.ids import new_command_id, new_correlation_id


def command(command_type: str = "Speak", key: str | None = None) -> Command:
    return Command(
        id=new_command_id(),
        type=command_type,
        payload={},
        correlation_id=new_correlation_id(),
        idempotency_key=key,
    )


async def test_command_returns_a_value() -> None:
    """**A Command has a result.** Use an Event if no result is needed."""
    bus = CommandBus()

    async def handler(cmd: Command) -> str:
        return f"ok:{cmd.type}"

    bus.register("Speak", handler)
    assert await bus.dispatch(command()) == "ok:Speak"


async def test_unknown_command_is_an_error() -> None:
    """**Never guessed at.**"""
    with pytest.raises(UnknownCommand):
        await CommandBus().dispatch(command("Nope"))


def test_a_type_can_only_have_one_handler() -> None:
    """If "dispatched to a single handler" broke, there'd be no way to tell which one ran."""
    bus = CommandBus()

    async def handler(cmd: Command) -> None:
        return None

    bus.register("Speak", handler)
    with pytest.raises(DuplicateHandler):
        bus.register("Speak", handler)


async def test_side_effecting_command_requires_an_idempotency_key() -> None:
    """**Closes off, starting in Phase 1, a precondition Crash Recovery (Phase 4a) needs.**"""
    bus = CommandBus()

    async def handler(cmd: Command) -> None:
        return None

    bus.register("WriteFile", handler, has_side_effect=True)

    with pytest.raises(MissingIdempotencyKey):
        await bus.dispatch(command("WriteFile"))

    await bus.dispatch(command("WriteFile", key="abc"))


async def test_hooks_run_in_registration_order() -> None:
    registry = HookRegistry()
    order: list[str] = []

    def make(name: str) -> Any:
        async def handler(payload: Mapping[str, Any]) -> Continue:
            order.append(name)
            return Continue()

        return handler

    registry.register(HookName.BEFORE_TOOL, make("first"))
    registry.register(HookName.BEFORE_TOOL, make("second"))

    outcome = await registry.run(HookName.BEFORE_TOOL, {})

    assert isinstance(outcome, Continue)
    assert order == ["first", "second"]


async def test_before_tool_veto_stops_the_rest() -> None:
    """**A `before_tool` veto stops tool execution** (the Kernel side of event-model.md test 10)."""
    registry = HookRegistry()
    reached: list[str] = []

    async def veto(payload: Mapping[str, Any]) -> Veto:
        return Veto(reason="policy")

    async def after(payload: Mapping[str, Any]) -> Continue:
        reached.append("after")
        return Continue()

    registry.register(HookName.BEFORE_TOOL, veto)
    registry.register(HookName.BEFORE_TOOL, after)

    outcome = await registry.run(HookName.BEFORE_TOOL, {})

    assert isinstance(outcome, Veto)
    assert outcome.reason == "policy"
    assert reached == []


async def test_veto_from_a_non_vetoable_hook_is_a_contract_error() -> None:
    """**`before_tool` is the only Hook that can veto.** A refusal elsewhere doesn't stop Core."""
    registry = HookRegistry()

    async def veto(payload: Mapping[str, Any]) -> Veto:
        return Veto(reason="nope")

    registry.register(HookName.BEFORE_SPEAK, veto)

    with pytest.raises(HookContractError):
        await registry.run(HookName.BEFORE_SPEAK, {})


async def test_running_an_unregistered_hook_continues() -> None:
    assert isinstance(await HookRegistry().run(HookName.ON_APP_START, {}), Continue)
