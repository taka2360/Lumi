"""Inspector. **docs/architecture/ui.md §5.**

> Being able to trace "why did it say that just now" is a design requirement.

Two things are being protected here: that **the divergence between a parent and its
children is visible**, and that **producing the view never delays barge-in**.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from lumi.agent.inspector import METHOD_INSPECTOR, InspectorPublisher, snapshot
from lumi.agent.latency import TurnLatency
from lumi.kernel.activity import (
    Activity,
    ActivityKind,
    ActivityProposal,
    ActivityState,
    Actor,
    new_idle_activity,
)
from lumi.kernel.arbiter import AttentionArbiter
from lumi.kernel.cancellation import Cancellable, Cancellation
from lumi.kernel.event import DomainEvent, EventBus
from lumi.kernel.ids import new_activity_id, new_correlation_id, new_event_id
from lumi.storage.events import SqliteEventStore
from lumi.storage.sqlite import Database
from lumi.transport.protocol import Role


class FakeNotifier:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.sent: list[dict[str, Any]] = []
        self._delay = delay

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        assert role is Role.STAGE
        assert method == METHOD_INSPECTOR
        if self._delay:
            await asyncio.sleep(self._delay)
        self.sent.append(payload or {})


def arbiter() -> AttentionArbiter:
    database = Database.open(":memory:")
    database.migrate()
    return AttentionArbiter(EventBus(SqliteEventStore(database)))


def conversation() -> ActivityProposal:
    return ActivityProposal(
        kind=ActivityKind.CONVERSATION,
        actor=Actor.USER_INITIATED,
        intent="reply",
        correlation_id=new_correlation_id(),
        deferrable=False,
    )


# ── The view itself ──────────────────────────────────────────


def test_foreground_comes_first() -> None:
    """**Its position must not move** as background Activities come and go — it is the
    one the reader is looking for.
    """
    idle = new_idle_activity(new_correlation_id())
    other = Activity(
        id=new_activity_id(),
        kind=ActivityKind.CONVERSATION,
        actor=Actor.USER_INITIATED,
        intent="reply",
        correlation_id=new_correlation_id(),
    )

    view = snapshot([idle, other], other.id, None)

    assert [item["foreground"] for item in view["activities"]] == [True, False]
    assert view["activities"][0]["id"] == str(other.id)


async def test_a_child_that_outlives_its_parent_is_visible() -> None:
    """★ **The divergence is the point of this view** (docs/architecture/ui.md §5).

    A parent already `cancelling` while a child has not stopped is exactly the state that
    is impossible to diagnose from logs alone.
    """
    kernel = arbiter()
    await kernel.start()
    accepted = await kernel.propose(conversation())
    activity = accepted.activity  # type: ignore[union-attr]
    activity.cancellables.append(
        Cancellable(id="tts", label="TTS 再生", contract=Cancellation.COOPERATIVE)
    )

    view = snapshot(list(kernel.activities()), kernel.current().id, None)
    foreground = view["activities"][0]

    assert foreground["state"] == ActivityState.RUNNING.value
    assert foreground["cancellables"] == [
        {"label": "TTS 再生", "contract": "cooperative", "finished": False}
    ]


async def test_idle_is_still_listed_while_suspended() -> None:
    """**idle never disappears.** A tree that hides it would suggest nothing is running."""
    kernel = arbiter()
    await kernel.start()
    await kernel.propose(conversation())

    view = snapshot(list(kernel.activities()), kernel.current().id, None)
    kinds = {item["kind"]: item["state"] for item in view["activities"]}

    assert kinds["idle"] == ActivityState.SUSPENDED.value
    assert kinds["conversation"] == ActivityState.RUNNING.value


def test_the_latency_breakdown_rides_along() -> None:
    latency = TurnLatency(
        correlation_id=new_correlation_id(),
        spans={"stt_ms": 68, "llm_first_token_ms": 537},
        total_ms=700,
        completed=True,
    )
    view = snapshot([], new_activity_id(), latency)
    assert view["latency"]["unaccounted_ms"] == 95
    assert view["latency"]["completed"] is True


def test_no_turn_yet_is_not_an_error() -> None:
    """**Before the first utterance there is simply nothing to show.**"""
    assert snapshot([], new_activity_id(), None)["latency"] is None


# ── Never on the critical path ─────────────────────────────


async def test_the_event_handler_never_awaits_the_send() -> None:
    """★ **Barge-in must not wait for a dev view to render.**

    `EventBus._dispatch` awaits its subscribers, and Activity transitions happen during
    preempt. A send inside the handler would sit between the user speaking and Lumi
    going quiet.
    """
    kernel = arbiter()
    await kernel.start()
    notifier = FakeNotifier(delay=1.0)
    publisher = InspectorPublisher(kernel, notifier, lambda: None)

    started = asyncio.get_running_loop().time()
    await publisher.on_event(_event())
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.05, "購読ハンドラが送信を待っている"
    assert notifier.sent == []


async def test_one_preempt_produces_one_snapshot() -> None:
    """A single preempt fires four transitions. **Sending four times would make the view
    flicker and waste the WS on the busiest moment of the turn.**
    """
    kernel = arbiter()
    await kernel.start()
    notifier = FakeNotifier()
    publisher = InspectorPublisher(kernel, notifier, lambda: None)
    publisher.start()
    try:
        for _ in range(4):
            await publisher.on_event(_event())
        await asyncio.sleep(0.2)
    finally:
        await publisher.stop()

    assert len(notifier.sent) == 1


async def test_a_broken_send_never_takes_anything_else_down() -> None:
    """**A dev view is never allowed to break the conversation.**"""

    class Broken(FakeNotifier):
        async def notify(self, role: Role, method: str, payload: Any = None) -> None:
            del role, method, payload
            raise RuntimeError("stage gone")

    kernel = arbiter()
    await kernel.start()
    publisher = InspectorPublisher(kernel, Broken(), lambda: None)
    publisher.start()
    try:
        await publisher.on_event(_event())
        await asyncio.sleep(0.2)
        assert publisher._task is not None
        assert not publisher._task.done(), "1回の失敗でループが死んでいる"
    finally:
        await publisher.stop()


def _event() -> DomainEvent:
    """A minimal event. **The publisher never reads it** — only that one arrived."""
    return DomainEvent(
        id=new_event_id(),
        stream_key="activity:x",
        sequence_id=1,
        causation_id=None,
        correlation_id=new_correlation_id(),
        type="ActivityStarted",
        payload={},
        occurred_at=datetime.now(UTC),
    )
