"""The SQLite skeleton, encrypted at rest.

**Memories on a user's PC must still be readable by a version released six months
from now** (.claude/rules/memory.md), so migration management is wired through from
Phase 1. **And nothing conversation-derived is written in the clear** (ADR-038 /
ADR-040), which is why the on-disk cases here all go through a key.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import sqlite_vec

from lumi.storage.events import EVENTS_SCHEMA
from lumi.storage.memory import MEMORY_SCHEMA
from lumi.storage.sqlite import IN_MEMORY, Database, Schema, StorageError, one

KEY = "ab" * 32
OTHER_KEY = "cd" * 32


def test_open_applies_migrations() -> None:
    db = Database.open(IN_MEMORY, EVENTS_SCHEMA)
    try:
        with db.transaction() as conn:
            version = one(conn.execute("SELECT version FROM _schema_version"))[0]
        assert version == EVENTS_SCHEMA.version
    finally:
        db.close()


def test_reopening_does_not_reapply(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    Database.open(path, EVENTS_SCHEMA, key=KEY).close()
    db = Database.open(path, EVENTS_SCHEMA, key=KEY)
    try:
        with db.transaction() as conn:
            rows = one(conn.execute("SELECT COUNT(*) FROM _schema_version"))[0]
        assert rows == 1
    finally:
        db.close()


def test_a_newer_schema_is_refused(tmp_path: Path) -> None:
    """**Never guesses and proceeds anyway.** The case of an older Lumi opening a DB a newer Lumi
    created.
    """
    path = tmp_path / "events.db"
    db = Database.open(path, EVENTS_SCHEMA, key=KEY)
    with db.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version = ?", (EVENTS_SCHEMA.version + 1,))
    db.close()

    with pytest.raises(StorageError):
        Database.open(path, EVENTS_SCHEMA, key=KEY)


def test_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    db = Database.open(path, EVENTS_SCHEMA, key=KEY)
    try:
        with pytest.raises(RuntimeError), db.transaction() as conn:
            conn.execute(
                "INSERT INTO events"
                " (id, stream_key, sequence_id, type, payload, correlation_id, occurred_at)"
                " VALUES ('x', 's', 1, 't', '{}', 'c', 'now')"
            )
            raise RuntimeError("boom")

        with db.transaction() as conn:
            count = one(conn.execute("SELECT COUNT(*) FROM events"))[0]
        assert count == 0
    finally:
        db.close()


def test_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "events.db"
    db = Database.open(path, EVENTS_SCHEMA, key=KEY)
    db.close()
    assert path.exists()


# ── Encryption ────────────────────────────────────────────────────────────────


def test_an_on_disk_database_without_a_key_is_refused(tmp_path: Path) -> None:
    """**There is no "plaintext just this once."** That is how the exception becomes the
    default (docs/contracts/privacy.md §3).
    """
    with pytest.raises(StorageError):
        Database.open(tmp_path / "events.db", EVENTS_SCHEMA)

    assert not (tmp_path / "events.db").exists()


def test_the_file_is_not_a_plaintext_sqlite_database(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    db = Database.open(path, EVENTS_SCHEMA, key=KEY)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO events"
            " (id, stream_key, sequence_id, type, payload, correlation_id, occurred_at)"
            " VALUES ('x', 's', 1, 'utterance', ?, 'c', 'now')",
            ('{"text": "SENTINEL"}',),
        )
    # **Checked while the connection is still open**, so the WAL is still on disk.
    # A committed row may live only in the sidecar until a checkpoint, and a plaintext
    # WAL next to an encrypted database would leak exactly what the database hides.
    assert b"SENTINEL" not in path.read_bytes()
    wal = Path(f"{path}-wal")
    assert wal.exists(), "WAL is expected to be enabled for on-disk databases"
    assert b"SENTINEL" not in wal.read_bytes()

    db.close()

    assert b"SENTINEL" not in path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(str(path)).execute("SELECT COUNT(*) FROM events").fetchone()


def test_a_wrong_key_is_refused_at_open(tmp_path: Path) -> None:
    """**Reported at open, not at whichever query happens to run first.**"""
    path = tmp_path / "events.db"
    Database.open(path, EVENTS_SCHEMA, key=KEY).close()

    with pytest.raises(StorageError):
        Database.open(path, EVENTS_SCHEMA, key=OTHER_KEY)


def test_the_extension_loads_inside_an_encrypted_database(tmp_path: Path) -> None:
    """The encrypted build is a different SQLite library. **sqlite-vec working against
    the stdlib one says nothing about this**, and the whole of memory rides on it.
    """
    db = Database.open(tmp_path / "memory.db", MEMORY_SCHEMA, key=KEY)
    try:
        db.load_extension(sqlite_vec.loadable_path())
        with db.transaction() as conn:
            conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[4])")
            vector = sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4])
            conn.execute("INSERT INTO v(rowid, embedding) VALUES (1, ?)", (vector,))
            hit = conn.execute(
                "SELECT rowid FROM v WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
                (vector,),
            ).fetchone()
        assert hit == (1,)
    finally:
        db.close()


def test_fts5_works_inside_an_encrypted_database(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "memory.db", MEMORY_SCHEMA, key=KEY)
    try:
        with db.transaction() as conn:
            conn.execute("CREATE VIRTUAL TABLE f USING fts5(body)")
            conn.execute("INSERT INTO f(body) VALUES ('Factorio が好き')")
            found = conn.execute("SELECT COUNT(*) FROM f WHERE f MATCH ?", ("Factorio",)).fetchone()
        assert found == (1,)
    finally:
        db.close()


def test_loading_an_extension_leaves_extension_loading_off(tmp_path: Path) -> None:
    """**Enabled only for the duration of the load.** Left on, any SQL reaching this
    connection could load a DLL.
    """
    db = Database.open(tmp_path / "memory.db", MEMORY_SCHEMA, key=KEY)
    try:
        db.load_extension(sqlite_vec.loadable_path())
        with pytest.raises(Exception, match="not authorized"):
            with db.transaction() as conn:
                conn.execute("SELECT load_extension(?)", (sqlite_vec.loadable_path(),))
    finally:
        db.close()


# ── One file per store (Phase 2c) ─────────────────────────────────────────────


def test_a_file_refuses_to_become_a_different_database(tmp_path: Path) -> None:
    """★ **The event database is not a half-empty memory database.**

    Opening one file with another schema would "work": the migrations would run, the
    tables would appear, and the events already in it would sit alongside episodes in a
    file two different parts of Core believe they own.
    """
    path = tmp_path / "events.db"
    Database.open(path, EVENTS_SCHEMA, key=KEY).close()

    with pytest.raises(StorageError, match=re.escape("storage.events")):
        Database.open(path, MEMORY_SCHEMA, key=KEY)


def test_the_component_is_recorded_with_the_version(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "memory.db", MEMORY_SCHEMA, key=KEY)
    try:
        with db.transaction() as conn:
            row = one(conn.execute("SELECT component, version FROM _schema_version"))
        assert row[0] == "storage.memory"
        assert row[1] == MEMORY_SCHEMA.version
    finally:
        db.close()


def test_a_schema_version_is_its_number_of_migrations() -> None:
    """**They cannot drift**: adding a migration is the only way to move the version."""
    schema = Schema(component="test", migrations=(("CREATE TABLE a (x)",), ("CREATE TABLE b (x)",)))
    assert schema.version == 2


def test_migrations_apply_in_order_and_only_once(tmp_path: Path) -> None:
    path = tmp_path / "staged.db"
    first = Schema(component="test", migrations=(("CREATE TABLE a (x)",),))
    Database.open(path, first, key=KEY).close()

    second = Schema(
        component="test",
        migrations=(("CREATE TABLE a (x)",), ("CREATE TABLE b (x)", "INSERT INTO a VALUES (1)")),
    )
    db = Database.open(path, second, key=KEY)
    try:
        with db.transaction() as conn:
            # **The first migration is not re-run** — it would fail on the existing table.
            assert one(conn.execute("SELECT COUNT(*) FROM a"))[0] == 1
            assert one(conn.execute("SELECT COUNT(*) FROM b"))[0] == 0
            assert one(conn.execute("SELECT version FROM _schema_version"))[0] == 2
    finally:
        db.close()
