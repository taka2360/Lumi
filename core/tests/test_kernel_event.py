"""EventBus numbering and persistence. **docs/contracts/event-model.md test table 1-5 / 8.**"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Iterator

import pytest

from lumi.kernel.event import (
    DomainEvent,
    DomainEventDraft,
    EventBus,
    SequenceChecker,
    SequenceError,
    Signal,
)
from lumi.kernel.ids import new_correlation_id
from lumi.storage.events import SqliteEventStore
from lumi.storage.sqlite import Database, StorageError


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database.open(":memory:")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def bus(database: Database) -> EventBus:
    return EventBus(SqliteEventStore(database))


def draft(stream_key: str = "activity:x", event_type: str = "ActivityStarted") -> DomainEventDraft:
    return DomainEventDraft(
        stream_key=stream_key,
        type=event_type,
        payload={"kind": "conversation"},
        correlation_id=new_correlation_id(),
    )


def test_signal_has_no_stream_key_or_sequence_id() -> None:
    """**Blocked at the type level** (Invariant 6). No path exists for external code to write a DomainEvent directly."""
    fields = {f.name for f in dataclasses.fields(Signal)}
    assert "stream_key" not in fields
    assert "sequence_id" not in fields


def test_draft_has_no_sequence_id() -> None:
    """**Using a separate type** guarantees the publisher never touches numbering."""
    assert "sequence_id" not in {f.name for f in dataclasses.fields(DomainEventDraft)}


async def test_sequence_starts_at_one_and_increases(bus: EventBus) -> None:
    first = await bus.publish(draft())
    second = await bus.publish(draft())
    assert (first.sequence_id, second.sequence_id) == (1, 2)


async def test_streams_are_numbered_independently(bus: EventBus) -> None:
    a = await bus.publish(draft("activity:a"))
    b = await bus.publish(draft("activity:b"))
    assert a.sequence_id == b.sequence_id == 1


async def test_concurrent_publish_has_no_gap_or_duplicate(bus: EventBus) -> None:
    """**Concurrent publishes to the same stream from multiple coroutines.**

    Confirms serialization happens on the Bus side, not by relying on "publishers being careful."
    """
    events = await asyncio.gather(*(bus.publish(draft("activity:same")) for _ in range(50)))
    numbers = sorted(event.sequence_id for event in events)
    assert numbers == list(range(1, 51))


async def test_events_are_persisted_before_dispatch(bus: EventBus, database: Database) -> None:
    """**Persists before dispatching.** Reversing this risks a crash where a subscriber saw a fact absent from history."""
    seen: list[int] = []

    async def handler(event: DomainEvent) -> None:
        with database.transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM events WHERE id = ?", (str(event.id),)
            ).fetchone()
        seen.append(int(row[0]))

    bus.subscribe(handler)
    await bus.publish(draft())
    assert seen == [1]


async def test_a_failing_subscriber_does_not_stop_the_bus(bus: EventBus) -> None:
    delivered: list[str] = []

    async def broken(event: DomainEvent) -> None:
        raise RuntimeError("boom")

    async def good(event: DomainEvent) -> None:
        delivered.append(event.type)

    bus.subscribe(broken)
    bus.subscribe(good)
    await bus.publish(draft())
    assert delivered == ["ActivityStarted"]


async def test_unsubscribe_stops_delivery(bus: EventBus) -> None:
    delivered: list[str] = []

    async def handler(event: DomainEvent) -> None:
        delivered.append(event.type)

    unsubscribe = bus.subscribe(handler)
    await bus.publish(draft())
    unsubscribe()
    await bus.publish(draft())
    assert len(delivered) == 1


async def test_payload_that_cannot_be_json_fails_loudly(bus: EventBus) -> None:
    """**Never silently dropped.** Treated as the publisher's design error."""
    bad = DomainEventDraft(
        stream_key="activity:x",
        type="Weird",
        payload={"obj": object()},
        correlation_id=new_correlation_id(),
    )
    with pytest.raises(StorageError):
        await bus.publish(bad)


def test_sequence_checker_detects_a_gap() -> None:
    checker = SequenceChecker()
    checker.check(_event("activity:x", 1))
    with pytest.raises(SequenceError):
        checker.check(_event("activity:x", 3))


def test_sequence_checker_detects_a_reversal() -> None:
    checker = SequenceChecker()
    checker.check(_event("activity:x", 1))
    checker.check(_event("activity:x", 2))
    with pytest.raises(SequenceError):
        checker.check(_event("activity:x", 2))


def test_sequence_checker_tracks_streams_separately() -> None:
    checker = SequenceChecker()
    checker.check(_event("activity:a", 1))
    checker.check(_event("activity:b", 1))
    checker.check(_event("activity:a", 2))


def _event(stream_key: str, sequence_id: int) -> DomainEvent:
    from datetime import UTC, datetime

    from lumi.kernel.ids import new_event_id

    return DomainEvent(
        id=new_event_id(),
        stream_key=stream_key,
        sequence_id=sequence_id,
        causation_id=None,
        correlation_id=new_correlation_id(),
        type="Whatever",
        payload={},
        occurred_at=datetime.now(UTC),
    )
