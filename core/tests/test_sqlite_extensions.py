"""Confirms sqlite-vec and FTS5 can be loaded.

roadmap Phase 0 verification step 13. **Noticing this in Phase 2 would take down the
entire memory feature with it**, so it's run continuously in CI starting from Phase 0.

All that's confirmed here is "does this work in this Python environment."
**Whether it also works in the bundled sidecar (PyInstaller) is a separate question**,
checked in Step M.
"""

from __future__ import annotations

import sqlite3

import sqlite_vec


def test_sqlite_vec_loads_and_answers_a_knn_query() -> None:
    db = sqlite3.connect(":memory:")
    try:
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)

        db.execute("create virtual table v using vec0(embedding float[4])")
        vector = sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4])
        db.execute("insert into v(rowid, embedding) values (1, ?)", [vector])

        rows = db.execute(
            "select rowid, distance from v where embedding match ? order by distance limit 1",
            [vector],
        ).fetchall()
        assert rows == [(1, 0.0)]
    finally:
        db.close()


def test_fts5_is_available() -> None:
    db = sqlite3.connect(":memory:")
    try:
        db.execute("create virtual table f using fts5(body)")
        db.execute("insert into f(body) values ('こんにちは 世界')")
        rows = db.execute("select rowid from f where f match '世界'").fetchall()
        assert rows == [(1,)]
    finally:
        db.close()
