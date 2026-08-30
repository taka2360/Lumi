"""A row of `memories`, as a record. **Converted explicitly, in one place.**

Design → docs/architecture/memory.md §3

SQLite columns are typed by whoever last wrote them, not by the schema, so every column
here is converted rather than trusted: a `confidence` that was written as an integer comes
back as one, and a `created_at` is a string until something makes it a datetime.

**The column list and the conversion have to move together.** They are separate statements
that agree only by position — `row[13]` is `valid_from` because `COLUMNS` says so, fifteen
lines earlier — so a column added to one and not the other silently shifts every field
after it. Kept in one file, that is a single edit; kept in two, it is a bug that reads as
a mis-parsed date.

## Reading is not writing

Nothing here writes. **The only writer of `memories` is `memory/store.py`** (ADR-045), and
that is checked statically rather than by convention — a query builder that can also write
would make the check unable to tell the difference.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import apsw

from lumi.memory.records import AssertionMode, MemoryRecord, MemoryType
from lumi.provenance import ProvenanceClass, TrustLevel

#: Every column of `memories`, in the order `hydrate` reads them. **Also the order an
#: `INSERT` binds**, which is why the two live in the same repository as one another.
COLUMNS: Final = (
    "id, type, subject, content, assertion_mode, confidence, provenance_class,"
    " trust_level, base_salience, created_at, last_accessed, access_count,"
    " archived_at, valid_from, superseded_by, embedding_model_id"
)


def read(conn: apsw.Connection, memory_id: str) -> MemoryRecord | None:
    """One record by id, or `None`. **Inside whatever transaction the caller opened.**"""
    row = conn.execute(f"SELECT {COLUMNS} FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return None if row is None else hydrate(conn, row)


def hydrate(conn: apsw.Connection, row: Any, *, evidence: bool = True) -> MemoryRecord:
    """One row as a record.

    `evidence=False` skips the two follow-up queries per row. **Only for callers that are
    about to throw the record away** — the fade sweep reads every live memory to ask
    `decay.is_faded`, and two extra queries per row there is the difference between one
    statement and several thousand.
    """
    memory_id = str(row[0])
    evidence_ref: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    if evidence:
        evidence_ref = tuple(
            str(item[0])
            for item in conn.execute(
                "SELECT utterance_id FROM memory_evidence WHERE memory_id = ?"
                " ORDER BY utterance_id",
                (memory_id,),
            ).fetchall()
        )
        sources = tuple(
            str(item[0])
            for item in conn.execute(
                "SELECT episode_id FROM memory_sources WHERE memory_id = ? ORDER BY episode_id",
                (memory_id,),
            ).fetchall()
        )
    return MemoryRecord(
        id=memory_id,
        type=MemoryType(str(row[1])),
        subject=str(row[2]),
        content=str(row[3]),
        assertion_mode=AssertionMode(str(row[4])),
        evidence_ref=evidence_ref,
        confidence=float(row[5]),
        provenance_class=ProvenanceClass(str(row[6])),
        trust_level=TrustLevel(str(row[7])),
        base_salience=float(row[8]),
        created_at=datetime.fromisoformat(str(row[9])),
        last_accessed=datetime.fromisoformat(str(row[10])),
        access_count=int(str(row[11])),
        archived_at=None if row[12] is None else datetime.fromisoformat(str(row[12])),
        valid_from=datetime.fromisoformat(str(row[13])),
        superseded_by=None if row[14] is None else str(row[14]),
        source_episode_ids=sources,
        embedding_model_id=str(row[15]),
    )
