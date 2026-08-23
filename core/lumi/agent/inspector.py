"""Inspector — **what is happening right now, and what it cost.**

Design → docs/architecture/ui.md §5

> **Being able to trace "why did it say that just now" is a design requirement.**
> Without it, Phase 6 cannot be tuned.

Phase 1 shows the minimum: the Activity tree and the latency breakdown.

## Why this never sends from inside the event handler

`EventBus._dispatch` **awaits** its subscribers, and Activity transitions happen during
preempt — which is the barge-in critical path. A WS send there would put the Inspector's
rendering between the user speaking and Lumi going quiet.

So the subscriber only **sets a flag**, and a background task does the sending. That also
coalesces the burst a single preempt produces (old → interrupt_requested, new → accepted,
new → running, old → cancelling) into **one** snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from typing import Any, Final, Protocol

from lumi import logging as lumi_logging
from lumi.agent.latency import TurnLatency
from lumi.kernel.activity import Activity
from lumi.kernel.event import DomainEvent
from lumi.kernel.ids import ActivityId
from lumi.transport.methods import METHOD_PANEL_INSPECTOR
from lumi.transport.protocol import Role

log = lumi_logging.get_logger(__name__)

#: How long to gather changes before sending [Provisional]. **Long enough to collapse one
#: preempt into a single snapshot, short enough to still read as live.**
COALESCE_S: Final = 0.05


class StageNotifier(Protocol):
    """Structural type for what this needs from `WsServer`. **Never the whole server** —
    the Inspector can notify and nothing else.
    """

    async def notify(
        self, role: Role, method: str, payload: dict[str, Any] | None = None
    ) -> None: ...


def activity_payload(activity: Activity, *, foreground: bool) -> dict[str, Any]:
    """One Activity, as the Inspector shows it.

    **The children's state is included even when it disagrees with the parent's** — an
    Activity that is `cancelling` while a child still runs is exactly the divergence this
    view exists to make visible (docs/architecture/ui.md §5).
    """
    return {
        "id": str(activity.id),
        "kind": activity.kind.value,
        "actor": activity.actor.value,
        "intent": activity.intent,
        "state": activity.state.value,
        "priority": activity.priority,
        "foreground": foreground,
        "cancellables": [
            {
                "label": item.label,
                "contract": item.contract.value,
                "finished": item.finished.is_set(),
            }
            for item in activity.cancellables
        ],
    }


def snapshot(
    activities: Sequence[Activity],
    foreground_id: ActivityId,
    latency: TurnLatency | None,
) -> dict[str, Any]:
    """The whole view. **A pure function** — takes no clock and touches no I/O.

    Ordered foreground first: it is the one the reader is looking for, and its position
    should not move as background Activities come and go.
    """
    ordered = sorted(activities, key=lambda item: item.id != foreground_id)
    return {
        "activities": [
            activity_payload(item, foreground=item.id == foreground_id) for item in ordered
        ],
        "latency": None if latency is None else latency.to_payload(),
    }


class InspectorPublisher:
    """Pushes the snapshot to the Stage. **Never on the critical path.**

    **Always published, even with nothing watching.** Gating it behind a flag would mean
    that the one time the view is wanted, Core has to be restarted to get it — and the
    payload is a handful of small dicts a few times per turn.
    """

    __slots__ = ("_arbiter", "_dirty", "_latency", "_notifier", "_task")

    def __init__(
        self,
        arbiter: Any,
        notifier: StageNotifier,
        latency: Callable[[], TurnLatency | None],
    ) -> None:
        self._arbiter = arbiter
        self._notifier = notifier
        self._latency = latency
        self._dirty = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="inspector")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def on_event(self, event: DomainEvent) -> None:
        """`EventBus` subscriber. **Only raises a flag** — never awaits a send.

        This runs inside `publish()`, which runs inside Activity transitions. Anything
        slow here delays barge-in.
        """
        del event
        self._dirty.set()

    async def _run(self) -> None:
        while True:
            await self._dirty.wait()
            # **Gather first, then send.** One preempt produces four transitions
            await asyncio.sleep(COALESCE_S)
            self._dirty.clear()
            try:
                await self.publish()
            except Exception:
                # A dev view failing must never take anything else down
                log.warning("inspector.publish_failed", exc_info=True)

    async def publish(self) -> None:
        payload = snapshot(
            list(self._arbiter.activities()), self._arbiter.current().id, self._latency()
        )
        await self._notifier.notify(Role.PANEL, METHOD_PANEL_INSPECTOR, payload)
