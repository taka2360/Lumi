"""SQLite implementation of `EventStore`.

Contract → docs/contracts/event-model.md "Numbering responsibility — the EventBus is
the sole numbering authority"

**Numbering (`MAX(sequence_id) + 1`) and the INSERT happen in the same transaction.**
Splitting them would let a crash in between create a gap, breaking the assumption that
"a gap means a genuine anomaly." `UNIQUE (stream_key, sequence_id)` is defense in
depth — its presence is not a reason to loosen the transaction.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime

from lumi.kernel.event import DomainEventDraft
from lumi.kernel.ids import EventId
from lumi.storage.sqlite import Database, StorageError


class SqliteEventStore:
    """Implementation of `lumi.kernel.event.EventStore`."""

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    async def append(
        self, event_id: EventId, draft: DomainEventDraft, occurred_at: datetime
    ) -> int:
        """**Blocking I/O is offloaded to a thread.** Never blocks the event loop."""
        return await asyncio.to_thread(self._append_blocking, event_id, draft, occurred_at)

    def _append_blocking(
        self, event_id: EventId, draft: DomainEventDraft, occurred_at: datetime
    ) -> int:
        try:
            # `allow_nan=False`: `NaN` / `Infinity` are not JSON, and json.dumps emits
            # them anyway. **Committing one produces a row no strict reader can parse** —
            # a failure that surfaces far from whoever published it
            payload = json.dumps(draft.payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            # A payload that can't be JSON-encoded is never silently dropped. **Treated as the
            # publisher's design error.**
            raise StorageError(f"payload を JSON にできない: {draft.type}") from error

        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence_id), 0) FROM events WHERE stream_key = ?",
                (draft.stream_key,),
            ).fetchone()
            sequence_id = int(row[0]) + 1
            try:
                conn.execute(
                    "INSERT INTO events"
                    " (id, stream_key, sequence_id, type, payload,"
                    "  correlation_id, causation_id, occurred_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(event_id),
                        draft.stream_key,
                        sequence_id,
                        draft.type,
                        payload,
                        str(draft.correlation_id),
                        draft.causation_id,
                        occurred_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                # A UNIQUE violation means serialization is broken. **Never overwrite or renumber.**
                raise StorageError(f"採番が衝突した: {draft.stream_key}#{sequence_id}") from error

        return sequence_id
