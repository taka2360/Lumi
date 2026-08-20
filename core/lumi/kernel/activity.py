"""Activity — the unit of "what Lumi is doing right now."

Single source of definition for the state machine → docs/contracts/state-machines.md
Priority values → docs/architecture/agent.md §1 (Decision → ADR-024)

**Only the Attention Arbiter may execute state transitions.** All that lives here is
the table of "which transitions are allowed" and the mechanism that fails on an
invalid one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from lumi.kernel.cancellation import Cancellable, CancelToken
from lumi.kernel.ids import ActivityId, CorrelationId, new_activity_id


class ActivityKind(StrEnum):
    CONVERSATION = "conversation"
    AUTONOMOUS = "autonomous"
    TASK = "task"
    GAME = "game"
    IDLE = "idle"


class Actor(StrEnum):
    """**Whose intent started this.**
    Also one of Policy's arguments (docs/architecture/permission.md).

    "A file read the user asked for" and "a file read Lumi did on its own" are
    different acts. This expresses that obvious fact in the type system.
    """

    USER_INITIATED = "user_initiated"
    SELF_INITIATED = "self_initiated"
    SCHEDULED = "scheduled"
    #: The idle Activity and Jobs. **Only L0 tools are usable**
    SYSTEM = "system"


class ActivityState(StrEnum):
    PROPOSED = "proposed"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    ACCEPTED = "accepted"
    #: **This is foreground. Exactly one at a time** (Invariant 4)
    RUNNING = "running"
    #: **Exclusive to idle.** idle takes this while another Activity holds foreground
    SUSPENDED = "suspended"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPT_REQUESTED = "interrupt_requested"
    CANCELLING = "cancelling"
    #: All children have stopped or completed
    CANCELLED = "cancelled"
    #: **Detached without waiting** for a `non_cancellable` child to complete
    ABANDONED = "abandoned"


# ── priority (ADR-024. Values are Provisional) ───────────────────────
# Spaced in steps of 10 so values can be inserted between them later.

_PRIORITY: Final[dict[ActivityKind, int]] = {
    ActivityKind.IDLE: 0,
    ActivityKind.AUTONOMOUS: 30,
    ActivityKind.TASK: 50,
    ActivityKind.GAME: 60,
    ActivityKind.CONVERSATION: 100,
}

_INTERRUPTIBLE_AT: Final[dict[ActivityKind, int]] = {
    #: Interruptible by everything
    ActivityKind.IDLE: 0,
    #: Interruptible only by user speech
    ActivityKind.AUTONOMOUS: 100,
    ActivityKind.TASK: 100,
    ActivityKind.GAME: 100,
    #: **Interruptible by a new user utterance (barge-in)**
    ActivityKind.CONVERSATION: 100,
}


def priority_of(kind: ActivityKind, actor: Actor) -> int:
    """Priority is **decided from the table.**
    A proposer (LLM, Stage, Extension) can never pass one in.

    This closes off any path for a claim like "this autonomous action is urgent"
    (upholding Invariant 1 on the Arbiter side too).

    The current table is determined by `kind` alone, but **`actor` is kept as an
    argument.** Priority is derived from facts Core decides — `kind` and `actor` —
    and the signature makes that derivation explicit (ADR-024).
    """
    del actor  # Unused by the current table. Kept in the signature for the reason above
    return _PRIORITY[kind]


def interruptible_at_of(kind: ActivityKind) -> int:
    """Interruptible by a proposal whose priority is **at or above** this value."""
    return _INTERRUPTIBLE_AT[kind]


def can_preempt(proposal: ActivityProposal, current: Activity) -> bool:
    """Whether preemption is allowed. **`>=`, not `>`.**

    barge-in is "a conversation interrupting a conversation" = **a preempt at equal
    priority**, which wouldn't work with `>`. **Between two of equal strength, the
    newer one wins.**

    Takes the proposal, not a number (ADR-024). `ActivityProposal.priority` is derived
    from `priority_of(kind, actor)` and cannot be set, so **there is no way to hand this
    a priority nobody decided** — which is what ADR-024 §3 ("priority を外部が提案できない")
    asks of the boundary. A plain `int` here left that open.
    """
    return proposal.priority >= current.interruptible_at


class InvalidTransition(RuntimeError):
    """A disallowed state transition. **Never swallowed** (evidence the state machine is broken)."""


#: Diagram → docs/contracts/state-machines.md "Activity state machine"
_ALLOWED: Final[dict[ActivityState, frozenset[ActivityState]]] = {
    ActivityState.PROPOSED: frozenset(
        {ActivityState.REJECTED, ActivityState.DEFERRED, ActivityState.ACCEPTED}
    ),
    ActivityState.REJECTED: frozenset(),
    #: Advances to accepted when re-proposed from the DeferredQueue
    ActivityState.DEFERRED: frozenset({ActivityState.ACCEPTED, ActivityState.REJECTED}),
    ActivityState.ACCEPTED: frozenset({ActivityState.RUNNING}),
    ActivityState.RUNNING: frozenset(
        {
            ActivityState.COMPLETING,
            ActivityState.INTERRUPT_REQUESTED,
            ActivityState.SUSPENDED,
        }
    ),
    #: idle returning to foreground / it may also never have left foreground since startup
    ActivityState.SUSPENDED: frozenset({ActivityState.RUNNING}),
    ActivityState.COMPLETING: frozenset({ActivityState.COMPLETED, ActivityState.FAILED}),
    ActivityState.COMPLETED: frozenset(),
    ActivityState.FAILED: frozenset(),
    ActivityState.INTERRUPT_REQUESTED: frozenset({ActivityState.CANCELLING}),
    ActivityState.CANCELLING: frozenset({ActivityState.CANCELLED, ActivityState.ABANDONED}),
    ActivityState.CANCELLED: frozenset(),
    ActivityState.ABANDONED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ActivityProposal:
    """A proposal to the Arbiter. **Carries no priority** (ADR-024)."""

    kind: ActivityKind
    actor: Actor
    # : What it's trying to do. Used by `DeferredQueue`'s deduplication (one entry per identical
    # kind × intent)
    intent: str
    correlation_id: CorrelationId
    # : Whether it may be re-proposed later if it can't be accepted now. True for autonomous, False
    # for user speech
    deferrable: bool = False
    deadline: datetime | None = None

    @property
    def priority(self) -> int:
        return priority_of(self.kind, self.actor)


@dataclass(slots=True)
class Activity:
    """**Only the Attention Arbiter may rewrite state** (via `_apply`)."""

    id: ActivityId
    kind: ActivityKind
    actor: Actor
    intent: str
    correlation_id: CorrelationId
    cancel_token: CancelToken = field(default_factory=CancelToken)
    deadline: datetime | None = None
    parent: ActivityId | None = None
    children: list[ActivityId] = field(default_factory=list)
    #: "Stoppable work" hanging off this Activity. Tool execution / LLM streams / TTS generation
    cancellables: list[Cancellable] = field(default_factory=list)
    _state: ActivityState = ActivityState.PROPOSED

    @property
    def state(self) -> ActivityState:
        return self._state

    @property
    def priority(self) -> int:
        return priority_of(self.kind, self.actor)

    @property
    def interruptible_at(self) -> int:
        return interruptible_at_of(self.kind)

    def _apply(self, new_state: ActivityState) -> None:
        """**Called only from the Attention Arbiter.** Tests verify it's never called from anywhere
        else.
        """
        if new_state not in _ALLOWED[self._state]:
            raise InvalidTransition(
                f"{self.kind}: invalid transition from {self._state} to {new_state}"
            )
        if new_state is ActivityState.SUSPENDED and self.kind is not ActivityKind.IDLE:
            # `suspended` is exclusive to idle. If anything else took it, "not
            # foreground but alive" instances would multiply, and Invariant 4's
            # "exactly one running" would lose its meaning.
            raise InvalidTransition(f"{self.kind}: only idle can transition to suspended")
        self._state = new_state


def new_idle_activity(correlation_id: CorrelationId) -> Activity:
    """idle at startup. **The sole exception that skips `proposed` / `accepted`**
    (state-machines.md).

    Constructed as `running` from the start. **This is an initial state, not a
    transition**, so it doesn't contradict "only the Arbiter executes state transitions."
    """
    return Activity(
        id=new_activity_id(),
        kind=ActivityKind.IDLE,
        actor=Actor.SYSTEM,
        intent="idle",
        correlation_id=correlation_id,
        _state=ActivityState.RUNNING,
    )
