"""Activity's priority and state-transition table.

**docs/architecture/agent.md tests 3b / 3c, docs/contracts/state-machines.md tests 1e / 4.**
Decision → ADR-024
"""

from __future__ import annotations

import dataclasses

import pytest

from lumi.kernel.activity import (
    Activity,
    ActivityKind,
    ActivityProposal,
    ActivityState,
    Actor,
    InvalidTransition,
    can_preempt,
    interruptible_at_of,
    new_idle_activity,
    priority_of,
)
from lumi.kernel.ids import new_activity_id, new_correlation_id


def make(kind: ActivityKind, actor: Actor = Actor.USER_INITIATED) -> Activity:
    return Activity(
        id=new_activity_id(),
        kind=kind,
        actor=actor,
        intent="test",
        correlation_id=new_correlation_id(),
    )


@pytest.mark.parametrize(
    ("kind", "actor", "expected"),
    [
        (ActivityKind.IDLE, Actor.SYSTEM, 0),
        (ActivityKind.AUTONOMOUS, Actor.SELF_INITIATED, 30),
        (ActivityKind.TASK, Actor.SCHEDULED, 50),
        (ActivityKind.GAME, Actor.USER_INITIATED, 60),
        (ActivityKind.CONVERSATION, Actor.USER_INITIATED, 100),
    ],
)
def test_priority_follows_the_table(kind: ActivityKind, actor: Actor, expected: int) -> None:
    """**The table is the sole definition** (docs/architecture/agent.md §1)."""
    assert priority_of(kind, actor) == expected


def test_conversation_preempts_conversation() -> None:
    """**barge-in is a preempt at equal priority.** This would fail with `>`."""
    current = make(ActivityKind.CONVERSATION)
    assert can_preempt(priority_of(ActivityKind.CONVERSATION, Actor.USER_INITIATED), current)


def test_autonomous_does_not_preempt_conversation() -> None:
    current = make(ActivityKind.CONVERSATION)
    assert not can_preempt(priority_of(ActivityKind.AUTONOMOUS, Actor.SELF_INITIATED), current)


def test_autonomous_does_not_preempt_another_autonomous() -> None:
    current = make(ActivityKind.AUTONOMOUS, Actor.SELF_INITIATED)
    assert not can_preempt(priority_of(ActivityKind.AUTONOMOUS, Actor.SELF_INITIATED), current)


def test_everything_preempts_idle() -> None:
    idle = new_idle_activity(new_correlation_id())
    for kind in ActivityKind:
        assert can_preempt(priority_of(kind, Actor.SYSTEM), idle)


def test_idle_is_interruptible_at_zero() -> None:
    assert interruptible_at_of(ActivityKind.IDLE) == 0


def test_proposal_cannot_carry_a_priority() -> None:
    """**A proposer can never pass in a priority** (ADR-024 / Invariant 1).

    Verifies it doesn't exist as a field. Derived properties are fine to read.
    """
    fields = {f.name for f in dataclasses.fields(ActivityProposal)}
    assert "priority" not in fields
    assert "interruptible_at" not in fields


def test_idle_starts_running_without_being_proposed() -> None:
    """**The sole exception that skips `proposed` / `accepted`** (state-machines.md)."""
    idle = new_idle_activity(new_correlation_id())
    assert idle.state is ActivityState.RUNNING


def test_suspended_is_only_for_idle() -> None:
    """If anything other than idle could take `suspended`, Invariant 4's "exactly one running" would
    loosen.
    """
    conversation = make(ActivityKind.CONVERSATION)
    conversation._apply(ActivityState.ACCEPTED)
    conversation._apply(ActivityState.RUNNING)
    with pytest.raises(InvalidTransition):
        conversation._apply(ActivityState.SUSPENDED)


def test_forbidden_transitions_raise() -> None:
    activity = make(ActivityKind.CONVERSATION)
    # Can't go directly from proposed to running (must pass through accepted)
    with pytest.raises(InvalidTransition):
        activity._apply(ActivityState.RUNNING)


def test_terminal_states_have_no_exit() -> None:
    activity = make(ActivityKind.CONVERSATION)
    activity._apply(ActivityState.ACCEPTED)
    activity._apply(ActivityState.RUNNING)
    activity._apply(ActivityState.COMPLETING)
    activity._apply(ActivityState.COMPLETED)
    with pytest.raises(InvalidTransition):
        activity._apply(ActivityState.RUNNING)
