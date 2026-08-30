"""Memories as a list a person reads. **Not as a conversation recalls them.**

Design → docs/architecture/memory.md §8 / docs/architecture/ui.md §5b

Retrieval ranks for a turn: what is worth having in front of Lumi right now, scored by
similarity, recency and salience. **This answers a different question.** Someone opening
the memory window is looking for the sentence they remember, and a semantic search would
return things that are *about* what they typed, in an order they cannot predict, with the
exact match possibly not first.

So this is substring matching and stable ordering — view mechanics — and it is kept apart
from `memory/store.py` because it has no business in a conversation and no business
writing. **Nothing here writes** (ADR-045).
"""

from __future__ import annotations

from typing import Any

from lumi.memory.records import MemoryRecord
from lumi.memory.rows import COLUMNS, hydrate
from lumi.storage.sqlite import Database, one


class MemoryBrowser:
    """Pages over `memories` for the memory window and the export. **Read only.**"""

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    async def browse(
        self,
        *,
        query: str = "",
        include_history: bool = False,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[MemoryRecord], int]:
        """A page of memories for the memory window, and how many there are in total.

        `include_history` brings in superseded and archived rows — **what Lumi used to
        believe.** Off by default: the list would otherwise be dominated by corrections
        of corrections, and the current belief is what people come to check.
        """
        if limit <= 0:
            return [], 0
        return await self._db.in_transaction(
            lambda conn: _page(conn, query.strip(), include_history, limit, max(offset, 0))
        )

    async def everything_after(
        self, *, after: MemoryRecord | None, limit: int
    ) -> list[MemoryRecord]:
        """The next page of **every memory, history included**, continuing from `after`.

        For the export, which reads the whole table. **`browse`'s `offset` cannot do that
        safely**: nothing is held between pages, so a memory written — or forgotten —
        while the export runs shifts every later row by one, and the record on the page
        boundary is written out twice or not at all. A cursor names the row it stopped
        at, so the next page is decided by what the table holds now rather than by how
        many rows it held before.

        **Not one long transaction instead.** `transaction()` is `BEGIN IMMEDIATE` on the
        one connection, so reading everything under a single one would stop reflection,
        retrieval and every other write for as long as the export takes.
        """
        if limit <= 0:
            return []
        cursor = (after.created_at.isoformat(), after.id) if after is not None else None
        return await self._db.in_transaction(lambda conn: _after(conn, cursor, limit))


def _page(
    conn: Any, query: str, include_history: bool, limit: int, offset: int
) -> tuple[list[MemoryRecord], int]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if not include_history:
        clauses.append("superseded_by IS NULL AND archived_at IS NULL")
    if query:
        # **`%` and `_` are escaped.** Typing a `%` into the search box otherwise
        # matches every memory, which reads as "the filter is broken".
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(subject LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')")
        parameters += [pattern, pattern]
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    total = int(one(conn.execute(f"SELECT COUNT(*) FROM memories{where}", parameters))[0])
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM memories{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (*parameters, limit, offset),
    ).fetchall()
    return [hydrate(conn, row) for row in rows], total


def _after(conn: Any, after: tuple[str, str] | None, limit: int) -> list[MemoryRecord]:
    # **The cursor is a pair of values, not a row.** `created_at` alone repeats — a
    # reflection pass writes several memories with one timestamp — and `id` alone
    # does not sort, so the tie is broken by the same `id DESC` the order uses.
    where = " WHERE (created_at, id) < (?, ?)" if after is not None else ""
    parameters: tuple[Any, ...] = after if after is not None else ()
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM memories{where} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*parameters, limit),
    ).fetchall()
    return [hydrate(conn, row) for row in rows]
