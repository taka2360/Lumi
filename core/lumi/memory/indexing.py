"""Keeping the index in step with what is believed.

Design → docs/architecture/memory.md §2 / Interface → docs/interfaces/memory.md

## One pass covers two jobs

A record needs embedding when it is new, and **again when the embedding model changes**.
Both show up the same way: `embedding_model_id` does not match the model that is loaded.
Treating them as one question is what makes a model swap a background chore rather than a
migration nobody wrote.

## The mark is written after the vector, never before

If the process dies between the two, the record simply looks unembedded and is done again.
The other order would leave a record claiming a vector that was never stored — findable in
theory, absent in practice, and **nothing would ever try again.**

## It is a Job, and it is allowed to be slow

Indexing runs in the background with a bounded batch. Embedding 500 memories at ~20 ms
each is ten seconds of CPU, and the thing sharing that CPU is capture, VAD and barge-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lumi import logging as lumi_logging
from lumi.memory.store import MemoryStore
from lumi.memory.vectors import MemoryIndex, document_text
from lumi.providers.embedding.base import EmbeddingProvider

log = lumi_logging.get_logger(__name__)

#: How many records one pass will embed [Provisional]. **Bounded**: a first run against a
#: long history should not hold a thread for a minute.
BATCH_LIMIT: Final = 64


@dataclass(frozen=True, slots=True)
class IndexReport:
    """What a pass did. **Zero is a result**, not a non-event."""

    embedded: int = 0
    remaining: bool = False


class Indexer:
    """Embeds memory records and puts them in the index."""

    __slots__ = ("_embedder", "_index", "_limit", "_store")

    def __init__(
        self,
        store: MemoryStore,
        index: MemoryIndex,
        embedder: EmbeddingProvider,
        *,
        limit: int = BATCH_LIMIT,
    ) -> None:
        self._store = store
        self._index = index
        self._embedder = embedder
        self._limit = limit

    async def run(self) -> IndexReport:
        """One bounded pass. **`remaining` says whether another is worth starting.**"""
        model_id = self._embedder.model_id()
        pending = await self._store.needing_embedding(model_id, limit=self._limit)
        if not pending:
            return IndexReport()

        # **Documents, not queries** (ADR-041). The instruction belongs to the search side;
        # adding it here would store vectors that no query can match properly.
        vectors = await self._embedder.embed_documents([document_text(r) for r in pending])
        await self._index.upsert(
            [
                (record.id, document_text(record), vector)
                for record, vector in zip(pending, vectors, strict=True)
            ]
        )
        await self._store.mark_embedded([record.id for record in pending], model_id)
        log.info("memory.indexed", count=len(pending), model=model_id)
        return IndexReport(embedded=len(pending), remaining=len(pending) == self._limit)

    async def run_until_done(self, *, max_passes: int = 32) -> IndexReport:
        """Repeat until nothing is pending. **Capped** — a pass that never reduces the queue
        (a model whose id keeps changing, a write racing the index) must not loop forever.
        """
        total = 0
        for _ in range(max_passes):
            report = await self.run()
            total += report.embedded
            if not report.remaining:
                return IndexReport(embedded=total, remaining=False)
        log.warning("memory.index_incomplete", embedded=total)
        return IndexReport(embedded=total, remaining=True)
