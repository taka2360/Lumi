"""Signal, DomainEvent, and the EventBus.

Single source of definition for the contract → docs/contracts/event-model.md

> **Something arriving from outside and something Core declares "happened" must
> never be the same type.**

## What's blocked at the type level

| What's blocked | How |
|---|---|
| External code writing a DomainEvent directly | `Signal` **has no** `stream_key` / `sequence_id` |
| Self-assigned numbering | `DomainEventDraft` has no `sequence_id` — only `EventBus` assigns one |

**Core decides** whether and how a `Signal` becomes a `DomainEvent`.
A Signal is raw material, not a fact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from lumi import logging as lumi_logging
from lumi.kernel.ids import ActivityId, CorrelationId, EventId, new_event_id
from lumi.provenance import TrustLevel

log = lumi_logging.get_logger(__name__)


# ── stream_key ────────────────────────────────────────────────
# Determined mechanically from "what the Event is about." **Never asks "does this
# need order guarantees?"** (that judgment is easy to get wrong →
# docs/contracts/event-model.md).


def activity_stream(activity_id: ActivityId) -> str:
    return f"activity:{activity_id}"


#: The streams below are **named but not yet published to.** They are the ordering
#: boundaries the event model already fixes (docs/contracts/event-model.md), and a stream
#: key invented later by whoever happens to need one first is how two producers end up
#: writing the same events under two names. Kept rather than deleted, and tagged so that
#: "unused" reads as "not this Phase" rather than as "dead".


def session_stream(session_id: str) -> str:
    """〔Phase 3〕Per-session ordering."""
    return f"session:{session_id}"


def memory_stream(subject: str) -> str:
    """〔Phase 3〕Per-subject ordering — one belief's history is one stream."""
    return f"memory:{subject}"


def world_stream(facet_key: str) -> str:
    """〔Phase 3〕Per-facet ordering (docs/architecture/world-state.md)."""
    return f"world:{facet_key}"


#: 〔Phase 4〕Grants and refusals, in the order they were decided.
PERMISSION_STREAM = "permission"
#: 〔Phase 6〕What Lumi decided to do of its own accord, and why.
AUTONOMY_STREAM = "autonomy"


@dataclass(frozen=True, slots=True)
class Signal:
    """A notification arriving at Core from outside (Shell / Stage / Extension / Widget).

    **This is not a fact.** It only becomes a DomainEvent once Core interprets it.
    The Stage never says "reduce the budget." It only reports "the user said it's noisy."
    """

    #: Who sent it. An authenticated peer identity
    source_id: str
    type: str
    payload: Mapping[str, Any]
    received_at: datetime
    #: Determined by the sender's trust level. Propagates to anything derived from this
    trust_level: TrustLevel

    # **Has neither** `stream_key` nor `sequence_id`. This is the type-level guarantee (Invariant
    # 6).


@dataclass(frozen=True, slots=True)
class DomainEventDraft:
    """Constructed by the publisher. **Carries no `sequence_id`.**

    Kept as a separate type so it's guaranteed at compile time that the publisher
    never touches numbering.
    """

    stream_key: str
    type: str
    payload: Mapping[str, Any]
    correlation_id: CorrelationId
    #: The Command / Signal / DomainEvent that caused this
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """A fact Core declares "happened." **Named in the past tense.**"""

    id: EventId
    stream_key: str
    #: Monotonically increasing within a stream_key. No gaps. **Only `EventBus` assigns this**
    sequence_id: int
    causation_id: str | None
    correlation_id: CorrelationId
    type: str
    payload: Mapping[str, Any]
    occurred_at: datetime


class EventStore(Protocol):
    """Persistence of DomainEvents. Implemented by `lumi.storage` (a Protocol to invert the
    dependency direction).
    """

    async def append(
        self, event_id: EventId, draft: DomainEventDraft, occurred_at: datetime
    ) -> int:
        """Assigns the next `sequence_id` for `stream_key`, persists it **in the same
        transaction**, and returns it.

        **Numbering and persistence must never be split apart.** Splitting them would
        let a crash in between create a gap, breaking the assumption that "a gap
        means a genuine anomaly" and rendering detection itself meaningless.
        """
        ...


Subscriber = Callable[[DomainEvent], Awaitable[None]]


class ReentrantPublishError(RuntimeError):
    """A subscriber published to the stream it was handling. **Refused, not awaited** (ADR-030).

    Waiting would deadlock (the per-stream lock is not reentrant) and the event has no
    valid place in the order anyway.
    """


class SequenceError(RuntimeError):
    """Detected a gap or an out-of-order sequence. **Never processed silently.**"""


class SequenceChecker:
    """For the consumer side. Checks sequential numbering per stream.

    **A tool to make sure gaps are never ignored.** Ignoring them would let Lumi keep
    running with a broken EventBus numbering scheme and nobody noticing.
    """

    __slots__ = ("_last",)

    def __init__(self) -> None:
        self._last: dict[str, int] = {}

    def check(self, event: DomainEvent) -> None:
        previous = self._last.get(event.stream_key)
        expected = 1 if previous is None else previous + 1
        if event.sequence_id != expected:
            raise SequenceError(f"{event.stream_key}: expected {expected}, got {event.sequence_id}")
        self._last[event.stream_key] = event.sequence_id


class EventBus:
    """**The sole numbering authority.** Serializes per stream_key.

    Multiple coroutines publishing to the same stream is bound to happen, so
    serialization is handled by the Bus itself instead of relying on "publishers
    being careful."
    """

    __slots__ = ("_clock", "_dispatching", "_locks", "_store", "_subscribers")

    def __init__(
        self,
        store: EventStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        #: Which task is mid-dispatch for each stream. **Only used to refuse re-entry**
        self._dispatching: dict[str, asyncio.Task[Any] | None] = {}
        self._subscribers: list[Subscriber] = []

    def subscribe(self, handler: Subscriber) -> Callable[[], None]:
        """Subscribes. Calling the returned function unsubscribes."""
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    async def publish(self, draft: DomainEventDraft) -> DomainEvent:
        """Number → persist → dispatch. **This order never changes.**

        Dispatching before persisting risks a crash where a subscriber observed a
        fact that never got persisted (the subscriber's state and the history would
        then disagree).

        **Dispatch happens under the same lock** (ADR-030). Releasing it first let a
        subscriber that awaits be overtaken by the next event on the same stream, and
        the contract is "same `stream_key`, ordered — delivery and processing both"
        (docs/contracts/event-model.md).
        """
        self._refuse_reentry(draft.stream_key)
        lock = self._locks.setdefault(draft.stream_key, asyncio.Lock())
        async with lock:
            event_id = new_event_id()
            occurred_at = self._clock()
            sequence_id = await self._store.append(event_id, draft, occurred_at)
            event = DomainEvent(
                id=event_id,
                stream_key=draft.stream_key,
                sequence_id=sequence_id,
                causation_id=draft.causation_id,
                correlation_id=draft.correlation_id,
                type=draft.type,
                payload=draft.payload,
                occurred_at=occurred_at,
            )
            self._dispatching[draft.stream_key] = asyncio.current_task()
            try:
                await self._dispatch(event)
            finally:
                del self._dispatching[draft.stream_key]
        return event

    def _refuse_reentry(self, stream_key: str) -> None:
        """**A subscriber may not publish to the stream it is currently handling** (ADR-030).

        The event it would publish has to be ordered *after* the one being handled, and
        that handling has not finished — so there is no ordering that satisfies the
        contract. The lock is not reentrant either, so allowing it means a **silent
        deadlock**: Lumi stops, and nothing anywhere says why.
        """
        if self._dispatching.get(stream_key) is asyncio.current_task():
            raise ReentrantPublishError(
                f"{stream_key}: cannot publish to stream currently being dispatched (ADR-030)"
            )

    async def _dispatch(self, event: DomainEvent) -> None:
        for handler in list(self._subscribers):
            try:
                await handler(event)
            except Exception:
                # **One subscriber's failure never stops the Bus.** But it's never silently dropped
                # either.
                log.exception(
                    "event.subscriber_failed",
                    stream_key=event.stream_key,
                    type=event.type,
                    sequence_id=event.sequence_id,
                )
