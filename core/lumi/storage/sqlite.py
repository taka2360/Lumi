"""SQLite connections and migrations. **Encrypted at rest.**

Databases holding conversation-derived data are encrypted on disk, with a random key
kept in the OS secret store (docs/contracts/privacy.md §3 / ADR-038). The stdlib
`sqlite3` module cannot open such a database, so Core uses APSW built against SQLite3
Multiple Ciphers instead (ADR-040). **`lumi.storage` is the only place that talks to
`apsw`**; everything else goes through `Database`.

## No plaintext database on disk

`open()` requires a key for any path other than `":memory:"`. There is no flag to
turn encryption off — "just this once, in plaintext" is how the exception becomes
the default. An in-memory database takes no key because it is never written down.

## What each database holds

| Database | Contents |
|---|---|
| Event DB | Kernel facts (Activity start/end, Tool's 3-stage record, permission) |
| Audit DB | The permission audit log |
| Memory DB | Episodes, memory records, vectors, the FTS index (Phase 2c onwards) |

**Utterance text is never put into a DomainEvent's payload.** Kernel facts and
conversation content stay in different places (docs/contracts/privacy.md §2).

## Transactions

The connection is left in autocommit, with **`BEGIN IMMEDIATE` made explicit.**
`IMMEDIATE` takes the write lock up front: with lazy acquisition another writer could
slip in between the numbering SELECT and the INSERT, and "numbering and persistence
happen in the same transaction" (docs/contracts/event-model.md) would silently stop
holding.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import apsw

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

#: The path that means "not written to disk". **The only path that may go unencrypted.**
MEMORY: Final = ":memory:"

#: Pinned explicitly rather than left to the library's default, so that a future
#: change of default cannot make existing databases unopenable. ChaCha20-Poly1305
#: (the sqleet scheme), which is what SQLite3 Multiple Ciphers uses by default today.
CIPHER: Final = "chacha20"

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
    """The DB can't be opened / the key is wrong / its schema is newer than expected.

    **Never silently degrades.**
    """


def one(cursor: apsw.Cursor) -> Any:
    """The single row a query was expected to return.

    `COUNT(*)` always produces a row, but nothing in the types says so, and writing
    `.fetchone()[0]` everywhere means a query that returns nothing surfaces as
    `TypeError: 'NoneType' is not subscriptable` from inside a transaction. **A missing
    row is a bug in the query, and this says so.**
    """
    row = cursor.fetchone()
    if row is None:
        raise StorageError("Query returned no row where exactly one was expected")
    return row


class Database:
    """A single connection. **Writes are serialized within the process.**

    APSW allows a connection to be used from more than one thread, which is what lets
    blocking I/O be handed to `asyncio.to_thread` (never blocking the event loop). In
    exchange, a lock guarantees it is never touched concurrently.
    """

    __slots__ = ("_conn", "_lock")

    def __init__(self, connection: apsw.Connection) -> None:
        self._conn = connection
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: Path | str, *, key: str | None = None) -> Database:
        """Opens the connection, unlocks it, and applies migrations.

        `key` is the hex database key from `lumi.storage.secret`. It is **required for
        any on-disk path**: a database Lumi writes to disk is encrypted, with no way to
        ask for otherwise. Pass `MEMORY` (with no key) for tests and for state that is
        deliberately not persisted.
        """
        on_disk = str(path) != MEMORY
        if on_disk and not key:
            raise StorageError(
                f"An on-disk database requires a key (docs/contracts/privacy.md §3): {path}"
            )
        if on_disk:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        try:
            connection = apsw.Connection(str(path))
        except apsw.Error as error:
            raise StorageError(f"Cannot open database: {path}") from error

        database = cls(connection)
        try:
            if key:
                database._unlock(key, path)
            connection.pragma("foreign_keys", "ON")
            if on_disk:
                # WAL has no effect on an in-memory database (and raises no error either).
                connection.pragma("journal_mode", "wal")
            database.migrate()
        except BaseException:
            connection.close()
            raise
        return database

    def _unlock(self, key: str, path: Path | str) -> None:
        """Applies the cipher and the key, then **proves the key is right.**

        SQLite3 Multiple Ciphers validates lazily: a wrong key is not reported by the
        `key` pragma but by the first page read. Reading the schema here turns "wrong
        key" into a failure at open, rather than a confusing error later from whichever
        query happened to run first.
        """
        try:
            self._conn.pragma("cipher", CIPHER)
            self._conn.pragma("key", key)
            self._conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()
        except apsw.Error as error:
            raise StorageError(
                f"Cannot unlock database (wrong key, or the file is not a Lumi database): {path}"
            ) from error

    @contextmanager
    def transaction(self) -> Iterator[apsw.Connection]:
        """`BEGIN IMMEDIATE` through `COMMIT`. `ROLLBACK` on failure."""
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

    def load_extension(self, path: str) -> None:
        """Loads a SQLite loadable extension (sqlite-vec).

        **Enabled only for the duration of the load.** Leaving extension loading on
        would mean any SQL that ever reaches this connection could load a DLL.
        """
        with self._lock:
            try:
                self._conn.enable_load_extension(True)
                self._conn.load_extension(path)
            except apsw.Error as error:
                raise StorageError(f"Cannot load SQLite extension: {path}") from error
            finally:
                self._conn.enable_load_extension(False)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
