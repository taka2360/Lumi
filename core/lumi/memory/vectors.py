"""The index: vectors in sqlite-vec, keywords in FTS5.

Interface → docs/interfaces/memory.md (`VectorStore`) / Model → ADR-041

## Two indexes because they fail differently

Vector search finds 「猫ちゃん元気?」 → 「ユーザーは猫を飼っている」 without either sharing a
word. Keyword search finds a name, a version number, an application — things an embedding
smooths away. **Neither subsumes the other**, which is why retrieval unions them.

## What the FTS index can and cannot do

The tokenizer is `trigram`, because `unicode61` splits on boundaries Japanese does not
have. The cost is that **a query shorter than three characters matches nothing** —「猫」
finds no rows here at all. That is not a bug to route around: it is the reason keyword
search is the second opinion and the vector search is the first.

## Indexed text is the text that was embedded

Both hold `subject: content`, the same string handed to the model. Indexing one form and
searching another is the kind of mismatch that shows up as "search feels slightly off"
rather than as an error.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import apsw
import numpy as np

from lumi import logging as lumi_logging
from lumi.memory.records import MemoryRecord
from lumi.storage.memory import EMBEDDING_DIMENSION
from lumi.storage.sqlite import Database

log = lumi_logging.get_logger(__name__)

#: How many neighbours to ask for beyond what the caller wants. **The live filter runs
#: after the KNN**, so archived and superseded rows consume slots; without the margin, a
#: user with a long history would get fewer results the more they had forgotten.
OVERFETCH: Final = 4

#: Shortest run of characters the trigram index can match.
MIN_TRIGRAM: Final = 3

#: Split on anything that is not a letter, digit or long-vowel mark. **Japanese is not
#: split further than this** — a real word segmenter is not available without a heavy
#: dependency, and trigram matching does not need one.
_SEPARATORS: Final = re.compile(r"[^0-9A-Za-z぀-ヿ一-鿿ー]+")


@dataclass(frozen=True, slots=True)
class ScoredId:
    """One hit. **`similarity` is cosine in [0, 1]** for vectors, and a normalized rank
    for keywords, so the two can be compared without knowing which index produced them.
    """

    memory_id: str
    similarity: float


def document_text(record: MemoryRecord) -> str:
    """What gets embedded and indexed for a record.

    **The subject is included.** Measured on a 10-item Japanese set: recall@3 went from
    90% to 100% when the field name was prepended (ADR-041). It also means a subject
    rename requires re-embedding — which is correct, because it changes the text.
    """
    return f"{record.subject}: {record.content}"


def keywords(text: str) -> list[str]:
    """Terms worth asking the trigram index for. **Pure.**

    Anything shorter than three characters is dropped rather than sent: FTS5's trigram
    tokenizer cannot match it, and passing it through would spend a query to learn that.
    """
    return [term for term in _SEPARATORS.split(text) if len(term) >= MIN_TRIGRAM]


def to_blob(vector: np.ndarray, *, dimension: int = EMBEDDING_DIMENSION) -> bytes:
    """A vector as sqlite-vec expects it: **contiguous float32.**

    The width is checked here because the failure otherwise arrives as a SQLite error
    about a blob length, several layers from whatever produced the wrong-sized vector.
    """
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    if values.shape[0] != dimension:
        raise ValueError(f"Expected a {dimension}-dimensional vector, got {values.shape[0]}")
    return values.tobytes()


class MemoryIndex:
    """Vectors and keywords for memory records. **Blocking I/O runs off the event loop.**

    Not a `MemoryStore`: this holds no beliefs, only the means of finding them. Deleting
    from here loses nothing a user would call a memory, which is why these tables are the
    one place in the memory database that gets rewritten freely.
    """

    __slots__ = ("_db", "_dimension")

    def __init__(self, database: Database, *, dimension: int = EMBEDDING_DIMENSION) -> None:
        self._db = database
        self._dimension = dimension

    def dimension(self) -> int:
        return self._dimension

    # ── writing ────────────────────────────────────────────────────────────

    async def upsert(self, entries: Sequence[tuple[str, str, np.ndarray]]) -> None:
        """`(memory_id, text, vector)` for each record. **Replaces what was there.**

        `vec0` refuses a duplicate primary key rather than replacing it, so re-indexing a
        record after an edit or a model change is a delete followed by an insert.
        """
        if not entries:
            return
        await self._db.in_transaction(lambda conn: self._upsert_in(conn, list(entries)))

    def _upsert_in(self, conn: apsw.Connection, entries: list[tuple[str, str, np.ndarray]]) -> None:
        for memory_id, text, vector in entries:
            blob = to_blob(vector, dimension=self._dimension)
            conn.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "INSERT INTO memory_vectors (memory_id, embedding) VALUES (?, ?)",
                (memory_id, blob),
            )
            conn.execute(
                "INSERT INTO memory_fts (content, memory_id) VALUES (?, ?)",
                (text, memory_id),
            )

    async def remove(self, memory_ids: Sequence[str]) -> None:
        """Drop these from the index. **Not a deletion of anything the user owns** — the
        record stays in `memories`; only the ability to find it by similarity goes.
        """
        if not memory_ids:
            return
        await self._db.in_transaction(lambda conn: self._remove_in(conn, tuple(memory_ids)))

    def _remove_in(self, conn: apsw.Connection, memory_ids: tuple[str, ...]) -> None:
        for memory_id in memory_ids:
            conn.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))

    async def count(self) -> int:
        return await asyncio.to_thread(self._count_blocking)

    def _count_blocking(self) -> int:
        with self._db.transaction() as conn:
            row = conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()
        return 0 if row is None else int(str(row[0]))

    # ── searching ──────────────────────────────────────────────────────────

    async def search(self, query: np.ndarray, k: int) -> list[ScoredId]:
        """Nearest live memories. **Archived and superseded rows are excluded.**

        A memory below the floor is one Lumi cannot currently recall (memory.md §5), and a
        superseded one is a belief it no longer holds. Returning either would make the
        forgetting curve decorative.
        """
        if k <= 0:
            return []
        return await asyncio.to_thread(
            self._search_blocking, to_blob(query, dimension=self._dimension), k
        )

    def _search_blocking(self, blob: bytes, k: int) -> list[ScoredId]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT v.memory_id, v.distance FROM memory_vectors v"
                " JOIN memories m ON m.id = v.memory_id"
                " WHERE v.embedding MATCH ? AND k = ?"
                "   AND m.archived_at IS NULL AND m.superseded_by IS NULL"
                " ORDER BY v.distance",
                (blob, k * OVERFETCH),
            ).fetchall()
        # The table is created with `distance_metric=cosine`, so this is 1 - cosine.
        return [
            ScoredId(memory_id=str(row[0]), similarity=1.0 - float(str(row[1]))) for row in rows[:k]
        ]

    async def search_keywords(self, text: str, k: int) -> list[ScoredId]:
        """Live memories matching any term of at least three characters.

        **An empty result is normal**, not a failure: a one-word Japanese query has no
        term long enough for the trigram index, and the vector search is what answers it.
        """
        terms = keywords(text)
        if not terms or k <= 0:
            return []
        return await asyncio.to_thread(self._keywords_blocking, terms, k)

    def _keywords_blocking(self, terms: Sequence[str], k: int) -> list[ScoredId]:
        # **Quoted, one phrase per term.** Unquoted input is FTS5 query syntax, where a
        # stray `*` or `OR` in what the user said would either change the query or raise.
        expression = " OR ".join(
            f'"{term}"' for term in (t.replace('"', "") for t in terms) if term
        )
        if not expression:
            return []
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT f.memory_id, f.rank FROM memory_fts f"
                " JOIN memories m ON m.id = f.memory_id"
                " WHERE memory_fts MATCH ?"
                "   AND m.archived_at IS NULL AND m.superseded_by IS NULL"
                " ORDER BY f.rank LIMIT ?",
                (expression, k),
            ).fetchall()
        # bm25 rank is negative and unbounded; **the absolute value carries no meaning
        # across queries**, so hits are scored by position instead of by rank.
        return [
            ScoredId(memory_id=str(row[0]), similarity=1.0 - index / max(len(rows), 1))
            for index, row in enumerate(rows)
        ]
