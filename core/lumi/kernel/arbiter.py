"""Attention Arbiter — the sole owner of "what's happening right now."

Design → docs/architecture/agent.md §1 / State machine → docs/contracts/state-machines.md

## Invariants upheld

1. **The Activity `_foreground` points to is always exactly one** (Invariant 4)
2. **Only foreground can hold `running`** (background holds `cancelling` / `suspended`)
3. **Only the Arbiter may execute Activity state transitions**
4. **An Activity is never interrupted through any path other than `propose` /
   `interrupt`** (muting the playback buffer is out of scope since it isn't an
   Activity state transition → docs/architecture/audio.md)

## Transition order during preempt

```
1. Old foreground → interrupt_requested
2. Immediate effects (e.g. muting TTS playback)   ← outside the Arbiter. Done
   synchronously by the VAD thread
3. New Activity → accepted
4. Switch _foreground to the new one              * Invariant 4 stays satisfied from here
5. New → running / Old → cancelling (background)
6. Old waits in background for its children to stop
```

**Never set the old one to `cancelling` before step 4.** `running` would briefly drop
to zero, breaking Invariant 4.
**Switching `_foreground` never waits for the old one's children to stop.** Waiting
would delay barge-in.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from lumi import logging as lumi_logging
from lumi.kernel.activity import (
    Activity,
    ActivityKind,
    ActivityProposal,
    ActivityState,
    can_preempt,
    new_idle_activity,
)
from lumi.kernel.cancellation import Cancellable, Cancellation, CancelToken
from lumi.kernel.event import DomainEventDraft, EventBus, activity_stream
from lumi.kernel.ids import ActivityId, JobId, new_activity_id, new_correlation_id
from lumi.kernel.job import Job

log = lumi_logging.get_logger(__name__)

#: How long the DeferredQueue holds entries [Provisional].
#: **Executing something Lumi wanted to say 30 minutes ago, right now, would be unsettling.**
DEFERRED_TTL: Final = timedelta(minutes=10)

#: How long to wait for a `cooperative` unit of work to stop [Provisional].
#: Past this, it's detached without waiting. **Waiting indefinitely would break barge-in.**
COOPERATIVE_GRACE_S: Final = 0.5


@dataclass(frozen=True, slots=True)
class Accepted:
    activity: Activity


@dataclass(frozen=True, slots=True)
class Deferred:
    retry_after: timedelta


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: str


ProposalOutcome = Accepted | Deferred | Rejected


@dataclass(frozen=True, slots=True)
class InterruptResult:
    """**Returns what stopped and what became abandoned.** Surfaced in the Inspector to make
    debugging possible.
    """

    activity_id: ActivityId
    final_state: ActivityState
    reason: str
    stopped: tuple[str, ...] = ()
    abandoned: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InferenceLease:
    """Valid only while the Job may perform inference. **Revoked on foreground inference.**"""

    job_id: JobId
    token: CancelToken


@dataclass(frozen=True, slots=True)
class _DeferredEntry:
    proposal: ActivityProposal
    offered_at: datetime


class DeferredQueue:
    """Holds proposals that couldn't be accepted, with a TTL. **Owned by the Arbiter.**"""

    __slots__ = ("_entries", "_ttl")

    def __init__(self, *, ttl: timedelta = DEFERRED_TTL) -> None:
        # : Keyed by (kind, intent). **One entry per identical kind × intent** (replaced by the
        # newer one)
        self._entries: dict[tuple[ActivityKind, str], _DeferredEntry] = {}
        self._ttl = ttl

    def offer(self, proposal: ActivityProposal, now: datetime) -> Deferred:
        self._entries[(proposal.kind, proposal.intent)] = _DeferredEntry(proposal, now)
        return Deferred(retry_after=self._ttl)

    def take_ready(self, now: datetime) -> list[ActivityProposal]:
        """Retrieves the ones still within their deadline. **Expired ones are silently dropped**
        (that's what TTL means).
        """
        ready: list[ActivityProposal] = []
        for (kind, intent), entry in self._entries.items():
            if now - entry.offered_at > self._ttl:
                log.info("arbiter.deferred_expired", kind=kind.value, intent=intent)
            else:
                ready.append(entry.proposal)
        self._entries.clear()
        return ready

    def __len__(self) -> int:
        return len(self._entries)


class AttentionArbiter:
    """**The only place allowed to execute Activity state transitions.**"""

    __slots__ = (
        "_activities",
        "_bus",
        "_clock",
        "_deferred",
        "_foreground",
        "_leases",
        "_pending",
    )

    def __init__(
        self,
        bus: EventBus,
        *,
        clock: Callable[[], datetime] | None = None,
        deferred_ttl: timedelta = DEFERRED_TTL,
    ) -> None:
        self._bus = bus
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._activities: dict[ActivityId, Activity] = {}
        self._deferred = DeferredQueue(ttl=deferred_ttl)
        self._leases: dict[JobId, InferenceLease] = {}
        self._foreground: ActivityId | None = None
        # : "Cleanup of the old Activity" running in the background. **Kept referenced or GC would
        # collect it**
        self._pending: set[asyncio.Task[InterruptResult]] = set()

    # ── Startup ──────────────────────────────────────────────

    async def start(self) -> Activity:
        """Constructs the idle Activity as `running` and makes it foreground.

        **`current()` never returns None** because this is always called at startup.
        """
        if self._foreground is not None:
            return self.current()
        idle = new_idle_activity(new_correlation_id())
        self._activities[idle.id] = idle
        self._foreground = idle.id
        await self._publish_started(idle)
        return idle

    # ── Access ──────────────────────────────────────────────

    def current(self) -> Activity:
        """**Never returns None.** Callers never need to write a null check."""
        if self._foreground is None:
            raise RuntimeError("Arbiter が start() されていない")
        return self._activities[self._foreground]

    def get(self, activity_id: ActivityId) -> Activity:
        return self._activities[activity_id]

    def activities(self) -> tuple[Activity, ...]:
        """Every Activity the Arbiter knows about. **Read-only.**

        What the Inspector reads the tree from (docs/architecture/ui.md §5). Several can
        exist at once without violating Invariant 4 — `cancelling` and `suspended` ones
        are not `running`, and **seeing that divergence is the point of the view.**

        Returned as a tuple so a caller cannot reach in and mutate the Arbiter's own list.
        State transitions remain the Arbiter's alone.
        """
        return tuple(self._activities.values())

    @property
    def deferred_count(self) -> int:
        return len(self._deferred)

    # ── Proposal ──────────────────────────────────────────────

    async def propose(self, proposal: ActivityProposal) -> ProposalOutcome:
        current = self.current()

        if not can_preempt(proposal.priority, current):
            if proposal.deferrable:
                return self._deferred.offer(proposal, self._clock())
            return Rejected(reason="busy")

        new = Activity(
            id=new_activity_id(),
            kind=proposal.kind,
            actor=proposal.actor,
            intent=proposal.intent,
            correlation_id=proposal.correlation_id,
            deadline=proposal.deadline,
        )
        self._activities[new.id] = new

        # 1. Begin stopping the old foreground (idle is preempted, not "stopped")
        interrupting = current.kind is not ActivityKind.IDLE
        if interrupting:
            current._apply(ActivityState.INTERRUPT_REQUESTED)

        # 3. New → accepted
        new._apply(ActivityState.ACCEPTED)

        # 4. * Switch foreground. Invariant 4 stays satisfied from here
        self._foreground = new.id

        # 5. New → running / Old → cancelling (or idle → suspended)
        new._apply(ActivityState.RUNNING)
        await self._publish_started(new)

        if interrupting:
            current._apply(ActivityState.CANCELLING)
            # 6. **Never waits for the old one's children to stop.** Waiting would delay barge-in
            task = asyncio.create_task(
                self._finish_cancelling(current, reason=f"preempted_by:{proposal.kind.value}")
            )
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        else:
            current._apply(ActivityState.SUSPENDED)

        return Accepted(new)

    def take_deferred(self) -> list[ActivityProposal]:
        """Retrieves held-back proposals, e.g. when foreground returns to idle.

        **Only retrieves them — never re-proposes here.** The caller (the
        Deliberative Loop) calls `propose()` again itself. If the Arbiter fired
        these on its own, the origin of "why did it say that just now" would end up
        hidden inside the Arbiter.
        """
        return self._deferred.take_ready(self._clock())

    # ── Interruption ──────────────────────────────────────────────

    async def interrupt(self, reason: str) -> InterruptResult:
        """Interrupts foreground and returns to idle. **The sole entry point for
        interrupting an Activity** (Invariant 4).

        "Sound stopping" does not go through here. The VAD thread sets `mute_flag`
        synchronously (docs/architecture/audio.md). **That's the part the user
        actually feels.**
        """
        current = self.current()
        if current.kind is ActivityKind.IDLE:
            # Cancelling idle is a no-op (state-machines.md)
            return InterruptResult(activity_id=current.id, final_state=current.state, reason=reason)

        current._apply(ActivityState.INTERRUPT_REQUESTED)
        await self._to_idle()
        current._apply(ActivityState.CANCELLING)
        return await self._finish_cancelling(current, reason=reason)

    async def _finish_cancelling(self, activity: Activity, *, reason: str) -> InterruptResult:
        activity.cancel_token.fire(reason)
        stopped, abandoned = await self._stop_cancellables(activity)

        final = ActivityState.ABANDONED if abandoned else ActivityState.CANCELLED
        activity._apply(final)
        await self._publish_ended(activity, reason=reason)

        result = InterruptResult(
            activity_id=activity.id,
            final_state=final,
            reason=reason,
            stopped=tuple(stopped),
            abandoned=tuple(abandoned),
        )
        log.info(
            "arbiter.interrupted",
            activity_id=str(activity.id),
            kind=activity.kind.value,
            final_state=final.value,
            reason=reason,
            stopped=list(stopped),
            abandoned=list(abandoned),
        )
        return result

    async def _stop_cancellables(self, activity: Activity) -> tuple[list[str], list[str]]:
        """Stops each one according to its contract.

        | Contract | Action |
        |---|---|
        | `hard` | Calls `kill()` immediately |
        | `cooperative` | Fires the token and waits only the grace period |
        | `non_cancellable` | **Detached without waiting** → abandoned |

        **A `cooperative` unit that doesn't stop within the grace period is also
        marked abandoned.** A warning is logged since it hints at a contract
        violation, but continuing to wait and breaking barge-in would be worse.
        """
        stopped: list[str] = []
        abandoned: list[str] = []
        waiting: list[Cancellable] = []

        for item in activity.cancellables:
            if item.contract is Cancellation.NON_CANCELLABLE:
                abandoned.append(item.label)
                continue
            item.cancel_token.fire("activity_cancelled")
            if item.contract is Cancellation.HARD:
                await self._kill(item, stopped, abandoned)
            else:
                waiting.append(item)

        for item in waiting:
            try:
                async with asyncio.timeout(COOPERATIVE_GRACE_S):
                    await item.finished.wait()
                stopped.append(item.label)
            except TimeoutError:
                log.warning(
                    "arbiter.cooperative_timeout",
                    activity_id=str(activity.id),
                    label=item.label,
                    grace_s=COOPERATIVE_GRACE_S,
                )
                abandoned.append(item.label)

        return stopped, abandoned

    async def _kill(self, item: Cancellable, stopped: list[str], abandoned: list[str]) -> None:
        assert item.kill is not None  # Guaranteed by Cancellable.__post_init__
        try:
            await item.kill()
        except Exception:
            # Couldn't be stopped. **Never treated as if it stopped.**
            log.exception("arbiter.kill_failed", label=item.label)
            abandoned.append(item.label)
        else:
            stopped.append(item.label)

    # ── Normal completion ───────────────────────────────────────────

    async def complete(self, activity_id: ActivityId, *, failed: bool = False) -> None:
        """Normal completion (or abnormal completion). Returns to idle if this was foreground."""
        activity = self._activities[activity_id]
        activity._apply(ActivityState.COMPLETING)
        if self._foreground == activity_id:
            await self._to_idle()
        activity._apply(ActivityState.FAILED if failed else ActivityState.COMPLETED)
        await self._publish_ended(activity, reason="failed" if failed else "completed")

    async def _to_idle(self) -> None:
        """Returns foreground to idle. **idle never disappears**, so this just wakes it."""
        idle = self._find_idle()
        self._foreground = idle.id
        if idle.state is ActivityState.SUSPENDED:
            idle._apply(ActivityState.RUNNING)

    def _find_idle(self) -> Activity:
        for activity in self._activities.values():
            if activity.kind is ActivityKind.IDLE:
                return activity
        raise RuntimeError("idle Activity が存在しない（start() されていない）")

    # ── Arbitrating the inference resource ──────────────────────────────────────

    @asynccontextmanager
    async def inference_lease(self, job: Job) -> AsyncIterator[InferenceLease]:
        """A `uses_inference` Job acquires this before running inference.

        Without it, a Reflection Job's inference would delay the conversation LLM's
        first token, **hitting the latency SLO (p95 2.0s) directly** (ADR-018).
        """
        lease = InferenceLease(job_id=job.id, token=job.cancel_token)
        self._leases[job.id] = lease
        try:
            yield lease
        finally:
            self._leases.pop(job.id, None)

    def request_inference(self, activity_id: ActivityId) -> None:
        """**Called when the foreground Activity starts inference.**
        Revokes any existing Job leases.

        A request from a non-foreground Activity is ignored (if background work
        could evict a Job, priority would lose its meaning).
        """
        if self._foreground != activity_id:
            return
        for lease in list(self._leases.values()):
            lease.token.fire("inference_revoked")

    # ── DomainEvent ───────────────────────────────────────

    async def _publish_started(self, activity: Activity) -> None:
        await self._publish(activity, "ActivityStarted", {})

    async def _publish_ended(self, activity: Activity, *, reason: str) -> None:
        await self._publish(
            activity, "ActivityEnded", {"final_state": activity.state.value, "reason": reason}
        )

    async def _publish(self, activity: Activity, event_type: str, extra: dict[str, object]) -> None:
        """**Never puts utterance text into the payload.**

        `intent` is an identifier for "what it's trying to do" (e.g. `user_speech`),
        not what the user actually said. Persisting conversation content is Phase 2
        (after `contracts/privacy.md` is written) → `lumi/storage/sqlite.py`
        """
        await self._bus.publish(
            DomainEventDraft(
                stream_key=activity_stream(activity.id),
                type=event_type,
                payload={
                    "kind": activity.kind.value,
                    "actor": activity.actor.value,
                    "intent": activity.intent,
                    "priority": activity.priority,
                    **extra,
                },
                correlation_id=activity.correlation_id,
            )
        )
