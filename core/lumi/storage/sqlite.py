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

## One file per store

| Database | Contents | Schema |
|---|---|---|
| `events.db` | Kernel facts (Activity, the Tool's 3-stage record, permission) | `storage.events` |
| `audit.db` | The permission audit log, and the record of what deletion removed | `storage.audit` |
| `memory.db` | Episodes and utterances; memory records and vectors follow | `storage.memory` |

**Utterance text is never put into a DomainEvent's payload.** Kernel facts and
conversation content stay in different places (docs/contracts/privacy.md §2), and
separate files are what make that checkable rather than merely intended.

The audit log is the reason the split is structural rather than tidy: it is append-only
to every Tool path, and deletable only by retention and by "erase everything"
(docs/contracts/privacy.md §5). **A separate file is what a deny rule can name.**

## Transactions

The connection is left in autocommit, with **`BEGIN IMMEDIATE` made explicit.**
`IMMEDIATE` takes the write lock up front: with lazy acquisition another writer could
slip in between the numbering SELECT and the INSERT, and "numbering and persistence
happen in the same transaction" (docs/contracts/event-model.md) would silently stop
holding.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import apsw

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

#: The path that means "not written to disk". **The only path that may go unencrypted.**
IN_MEMORY: Final = ":memory:"

#: Pinned explicitly rather than left to the library's default, so that a future
#: change of default cannot make existing databases unopenable. ChaCha20-Poly1305
#: (the sqleet scheme), which is what SQLite3 Multiple Ciphers uses by default today.
CIPHER: Final = "chacha20"


@dataclass(frozen=True, slots=True)
class Schema:
    """What one database file contains, as an ordered list of migrations.

    **The version is the number of migrations**, so a schema and its version cannot drift:
    adding a migration is the only way to move it. Applying index 0 produces version 1.

    `component` is stamped into the file. Opening `events.db` expecting the memory schema
    then fails at open instead of announcing that a dozen tables are missing.
    """

    component: str
    #: **Existing entries are never rewritten** (append-only). A change is a new entry.
    migrations: tuple[tuple[str, ...], ...]

    @property
    def version(self) -> int:
        return len(self.migrations)


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

    __slots__ = ("_conn", "_lock", "_schema")

    def __init__(self, connection: apsw.Connection, schema: Schema) -> None:
        self._conn = connection
        self._schema = schema
        self._lock = threading.Lock()

    @property
    def schema(self) -> Schema:
        return self._schema

    @classmethod
    def open(
        cls,
        path: Path | str,
        schema: Schema,
        *,
        key: str | None = None,
        extensions: Sequence[str] = (),
    ) -> Database:
        """Opens the connection, unlocks it, and applies `schema`'s migrations.

        `key` is the hex database key from `lumi.storage.secret`. It is **required for
        any on-disk path**: a database Lumi writes to disk is encrypted, with no way to
        ask for otherwise. Pass `IN_MEMORY` (with no key) for tests and for state that is
        deliberately not persisted.

        `extensions` are loaded **before the migrations run**, because a migration can
        create a table the extension provides (`vec0`). Loading them afterwards would fail
        on a fresh database and succeed on an existing one — the worst kind of difference,
        since only new users would ever see it.
        """
        on_disk = str(path) != IN_MEMORY
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

        database = cls(connection, schema)
        try:
            if key:
                database._unlock(key, path)
            for extension in extensions:
                database.load_extension(extension)
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

    async def in_transaction[T](self, work: Callable[[apsw.Connection], T]) -> T:
        """One transaction, off the event loop. **`BEGIN` through `COMMIT` in one hop.**

        Every DAO here was the same four lines: an `async` method that hands a
        `_blocking` twin to `asyncio.to_thread`, and a twin whose whole body is
        `with self.transaction() as conn`. Written out per method, the shape invites the
        one arrangement that must never appear:

            with db.transaction():              # holds a threading.Lock ...
                await asyncio.to_thread(work)   # ... across an await

        That holds the connection lock while the event loop runs other coroutines, and
        puts the transaction boundary on a different thread from the work it wraps.
        Passing the callable in instead makes the correct order the only expressible one.

        **Cancellation does not reach `work`.** `to_thread` abandons the await, not the
        thread, so a cancelled caller still leaves a committed transaction behind. That
        is unchanged from doing it by hand, and `retention` depends on it — a partial
        "erase everything" would be worse than a slow one.
        """

        def once() -> T:
            with self.transaction() as conn:
                return work(conn)

        return await asyncio.to_thread(once)

    def migrate(self, now: datetime | None = None) -> None:
        """Applies unapplied migrations in order. **No downgrade path exists.**

        **Memories on a user\'s PC must still be readable by a version released six months
        from now**, which is why every file carries its own version rather than relying on
        the code and the file happening to match.
        """
        schema = self._schema
        applied_at = (now or datetime.now(UTC)).isoformat()
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _schema_version ("
                " component  TEXT PRIMARY KEY,"
                " version    INTEGER NOT NULL,"
                " applied_at TEXT NOT NULL)"
            )
            row = conn.execute("SELECT component, version FROM _schema_version").fetchone()
            if row is not None and row[0] != schema.component:
                # **Never migrate a file into being something else.** Applying the memory
                # schema to the event database would produce a file that opens and is wrong.
                raise StorageError(f"This database holds {row[0]!r}, not {schema.component!r}")
            current = int(row[1]) if row is not None else 0

            if current > schema.version:
                # A newer Lumi's DB was opened by an older Lumi. **Never guess and proceed anyway.**
                raise StorageError(
                    f"Database schema version {current} is newer than "
                    f"this Lumi version ({schema.version})"
                )

            for statements in schema.migrations[current:]:
                for statement in statements:
                    conn.execute(statement)

            if row is None:
                conn.execute(
                    "INSERT INTO _schema_version (component, version, applied_at) VALUES (?, ?, ?)",
                    (schema.component, schema.version, applied_at),
                )
            elif current != schema.version:
                # **Only when something was actually applied.** Stamping the row on every
                # open would make `applied_at` mean "last started", and the one question it
                # exists to answer — when did this database become version N — unanswerable.
                conn.execute(
                    "UPDATE _schema_version SET version = ?, applied_at = ? WHERE component = ?",
                    (schema.version, applied_at, schema.component),
                )

        if current != schema.version:
            log.info(
                "storage.migrated",
                component=schema.component,
                from_version=current,
                to_version=schema.version,
            )

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
