"""SQLite connection and migrations.

**Phase 1 only persists DomainEvents.** sqlite-vec / FTS5 / memory tables are Phase 2.
The reason they aren't built first is that the memory schema depends on the privacy
policy (docs/roadmap.md Phase 2 🔴), and writing it before that policy is decided
would mean rebuilding it later.

## What Phase 1 persists / doesn't persist

| Persisted | Not persisted |
|---|---|
| Kernel facts (Activity start/end, Tool's 3-stage record, permission) | **Utterance text** |

**Utterance text is never put into a DomainEvent's payload.** Phase 1's Working Memory
lives in in-session memory only; persisting conversation starts in Phase 2 (after
`contracts/privacy.md` is written). Starting to write utterance text here first would
grow "a log nobody knows how to delete" before the policy is even decided.

## Transactions

Set to `isolation_level=None` (autocommit), with **`BEGIN IMMEDIATE` made explicit.**
Python's implicit transaction handling treats DDL and SELECT in ways that don't match
intuition, and it becomes impossible to tell from the code whether "numbering and
persistence happen in the same transaction" (docs/contracts/event-model.md) is
actually honored.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

# : The current schema version. When a migration is added, **this must match `_MIGRATIONS`'s
# length**.
SCHEMA_VERSION: Final = 2

# : Applying index 0 produces schema version 1. **Existing entries are never rewritten**
# (append-only).
_MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    (
        """
        CREATE TABLE events (
            id             TEXT    PRIMARY KEY,
            stream_key     TEXT    NOT NULL,
            sequence_id    INTEGER NOT NULL,
            type           TEXT    NOT NULL,
            payload        TEXT    NOT NULL,
            correlation_id TEXT    NOT NULL,
            causation_id   TEXT,
            occurred_at    TEXT    NOT NULL,
            UNIQUE (stream_key, sequence_id)
        )
        """,
        "CREATE INDEX events_by_stream ON events (stream_key, sequence_id)",
        "CREATE INDEX events_by_correlation ON events (correlation_id)",
    ),
    (
        # The audit log. **append-only** (docs/architecture/permission.md §7).
        # `prev_hash` / `record_hash` are added as a migration in Phase 4a.
        # They aren't added now because **an unused column is never added "for the future."**
        """
        CREATE TABLE audit_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                  TEXT NOT NULL,
            actor               TEXT NOT NULL,
            activity_id         TEXT NOT NULL,
            correlation_id      TEXT NOT NULL,
            capability          TEXT NOT NULL,
            security_scope_json TEXT NOT NULL,
            raw_input_digest    TEXT NOT NULL,
            decision            TEXT NOT NULL,
            reason              TEXT NOT NULL,
            policy_version      TEXT NOT NULL,
            policy_rule_id      TEXT NOT NULL,
            grant_id            TEXT,
            tool                TEXT NOT NULL,
            args_digest         TEXT NOT NULL,
            result_digest       TEXT,
            provenance_class    TEXT,
            trust_level         TEXT
        )
        """,
        "CREATE INDEX audit_by_activity ON audit_log (activity_id)",
        "CREATE INDEX audit_by_ts ON audit_log (ts)",
    ),
)


class StorageError(RuntimeError):
    """The DB can't be opened / its schema is newer than expected. **Never silently degrades.**"""


class Database:
    """A single connection. **Writes are serialized within the process.**

    `check_same_thread=False` is set so blocking I/O can be offloaded to
    `asyncio.to_thread` (never blocking the event loop). In exchange, a lock
    guarantees it is never touched concurrently.
    """

    __slots__ = ("_conn", "_lock")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: Path | str) -> Database:
        """Opens the connection and applies migrations. Pass `":memory:"` for testing."""
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        except sqlite3.Error as error:
            raise StorageError(f"Cannot open database: {path}") from error

        connection.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            # WAL has no effect on `:memory:` (and doesn't error either).
            connection.execute("PRAGMA journal_mode = WAL")

        database = cls(connection)
        database.migrate()
        return database

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """`BEGIN IMMEDIATE` through `COMMIT`. `ROLLBACK` on failure.

        `IMMEDIATE` is used to acquire the write lock up front.
        With lazy acquisition, another writer could slip in between the numbering
        SELECT and the INSERT.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def migrate(self) -> None:
        """Applies unapplied migrations in order. **No downgrade path exists.**"""
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM _schema_version").fetchone()
            current = int(row[0]) if row else 0

            if current > SCHEMA_VERSION:
                # A newer Lumi's DB was opened by an older Lumi. **Never guess and proceed anyway.**
                raise StorageError(
                    f"Database schema version {current} is newer than "
                    f"this Lumi version ({SCHEMA_VERSION})"
                )

            for statements in _MIGRATIONS[current:]:
                for statement in statements:
                    conn.execute(statement)

            if row is None:
                conn.execute("INSERT INTO _schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE _schema_version SET version = ?", (SCHEMA_VERSION,))

        if current != SCHEMA_VERSION:
            log.info("storage.migrated", from_version=current, to_version=SCHEMA_VERSION)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
