"""`EventStore` の SQLite 実装。

契約 → docs/contracts/event-model.md「採番責任 — EventBus が唯一の採番者」

**採番（`MAX(sequence_id) + 1`）と INSERT を同一トランザクションで行う。**
分けると、その間のクラッシュでギャップが生まれ、「ギャップ = 真の異常」という
前提が崩れる。`UNIQUE (stream_key, sequence_id)` は多層防御であって、
これがあるからトランザクションを緩めてよいという話ではない。
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
    """`lumi.kernel.event.EventStore` の実装。"""

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    async def append(
        self, event_id: EventId, draft: DomainEventDraft, occurred_at: datetime
    ) -> int:
        """**ブロッキング I/O はスレッドに逃がす。** イベントループを止めない。"""
        return await asyncio.to_thread(self._append_blocking, event_id, draft, occurred_at)

    def _append_blocking(
        self, event_id: EventId, draft: DomainEventDraft, occurred_at: datetime
    ) -> int:
        try:
            payload = json.dumps(draft.payload, ensure_ascii=False)
        except (TypeError, ValueError) as error:
            # JSON にできない payload を黙って捨てない。**発行側の設計ミスとして落とす。**
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
                # UNIQUE 違反 = 直列化が壊れている。**上書きも再採番もしない。**
                raise StorageError(f"採番が衝突した: {draft.stream_key}#{sequence_id}") from error

        return sequence_id
