"""The SQLite skeleton.

**Memories on a user's PC must still be readable by a version released six months
from now** (.claude/rules/memory.md). Migration management is wired through starting in Phase 1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lumi.storage.sqlite import SCHEMA_VERSION, Database, StorageError


def test_open_applies_migrations() -> None:
    db = Database.open(":memory:")
    try:
        with db.transaction() as conn:
            version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        db.close()


def test_reopening_does_not_reapply(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    Database.open(path).close()
    db = Database.open(path)
    try:
        with db.transaction() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM _schema_version").fetchone()[0]
        assert rows == 1
    finally:
        db.close()


def test_a_newer_schema_is_refused(tmp_path: Path) -> None:
    """**Never guesses and proceeds anyway.** The case of an older Lumi opening a DB a newer Lumi created."""
    path = tmp_path / "events.db"
    db = Database.open(path)
    with db.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    db.close()

    with pytest.raises(StorageError):
        Database.open(path)


def test_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    db = Database.open(path)
    try:
        with pytest.raises(RuntimeError), db.transaction() as conn:
            conn.execute(
                "INSERT INTO events"
                " (id, stream_key, sequence_id, type, payload, correlation_id, occurred_at)"
                " VALUES ('x', 's', 1, 't', '{}', 'c', 'now')"
            )
            raise RuntimeError("boom")

        with db.transaction() as conn:
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 0
    finally:
        db.close()


def test_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "events.db"
    db = Database.open(path)
    db.close()
    assert path.exists()
