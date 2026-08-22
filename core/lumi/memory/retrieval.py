"""Remembering: which memories reach the prompt, and why.

Design → docs/architecture/memory.md §7 / Interface → docs/interfaces/memory.md

## Three sources, because each misses something different

| | finds | misses |
|---|---|---|
| Vector | 「猫ちゃん元気?」 → 「猫を飼っている」 | exact names, versions, spellings |
| Keyword (trigram) | a product name said once | anything under three characters, and paraphrase |
| Recent | what was just written, before it is indexed | everything older |

## The score is multiplied by the grounds, not added to

`assertion_weight` is a multiplier (1.2 / 1.0 / 0.8 / 0.6 / 0.7). As an addend, a memory
with similarity 0 would surface on its grounds alone — a confident guess about nothing in
particular outranking the thing the user actually asked about.

## Everything about the cut is deterministic

**The LLM is never asked which memories to keep.** Scores are computed, sorted, and packed
until the budget runs out; what did not fit is returned as `dropped` with its breakdown, so
the Inspector can answer "why was this not remembered" (docs/interfaces/memory.md).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from lumi import logging as lumi_logging
from lumi.memory import decay
from lumi.memory.records import ASSERTION_WEIGHT, MemoryRecord
from lumi.memory.store import MemoryStore
from lumi.memory.vectors import MemoryIndex
from lumi.providers.embedding.base import EmbeddingProvider

log = lumi_logging.get_logger(__name__)

#: Weights of the score [Provisional]. **Similarity dominates**: the other two are
#: tie-breakers among things that are already relevant, and a memory that has nothing to do
#: with what was said should not arrive because it is fresh.
WEIGHT_SIMILARITY: Final = 0.6
WEIGHT_RECENCY: Final = 0.15
WEIGHT_SALIENCE: Final = 0.25

#: How fast "recent" stops counting. Not the decay curve — that is `decay.TAU` and it
#: measures forgetting. This measures *topicality*: what was said this week is part of the
#: current conversation in a way last spring is not.
RECENCY_TAU: Final = timedelta(days=30)

#: How many candidates each source contributes [Provisional].
VECTOR_K: Final = 8
KEYWORD_K: Final = 8
RECENT_K: Final = 4


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Why a memory scored what it did. **Every term, not just the total.**

    Without the parts, tuning in Phase 6 is guesswork: "this memory keeps winning" has
    four possible causes and they need different fixes.
    """

    similarity: float
    recency: float
    effective_salience: float
    assertion_weight: float
    total: float
    #: Which sources produced this candidate — `vector`, `keyword`, `recent`.
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    record: MemoryRecord
    breakdown: ScoreBreakdown

    @property
    def score(self) -> float:
        return self.breakdown.total


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """What was remembered, what was not, and why for both."""

    selected: tuple[ScoredMemory, ...] = ()
    #: Scored but did not fit the budget. **Reported, never silently discarded.**
    dropped: tuple[ScoredMemory, ...] = ()
    #: Milliseconds spent embedding the query. **On the turn's critical path**
    #: (docs/architecture/audio.md §7).
    embed_ms: float = 0.0
    #: Set when the embedding Provider was unavailable and only keywords and recency ran.
    degraded: bool = False

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(item.record for item in self.selected)


def recency(record: MemoryRecord, now: datetime, *, tau: timedelta = RECENCY_TAU) -> float:
    """How current this belief is, in [0, 1]. **Pure.**

    Measured from `valid_from` — when the belief started being true — rather than from
    `created_at`. A memory written today about something the user said last year is not
    news, and `valid_from` is the field that knows the difference.
    """
    elapsed = max((now - record.valid_from).total_seconds(), 0.0)
    return math.exp(-elapsed / tau.total_seconds())


def score(
    record: MemoryRecord, similarity: float, now: datetime, *, sources: Sequence[str] = ()
) -> ScoreBreakdown:
    """The retrieval score for one memory. **Pure, and the only place the formula lives.**"""
    effective = decay.effective_salience(record, now)
    freshness = recency(record, now)
    weight = ASSERTION_WEIGHT[record.assertion_mode]
    total = (
        WEIGHT_SIMILARITY * similarity + WEIGHT_RECENCY * freshness + WEIGHT_SALIENCE * effective
    ) * weight
    return ScoreBreakdown(
        similarity=similarity,
        recency=freshness,
        effective_salience=effective,
        assertion_weight=weight,
        total=total,
        sources=tuple(sources),
    )


def pack_into_budget(
    scored: Sequence[ScoredMemory],
    budget_tokens: int,
    cost: Callable[[MemoryRecord], int],
) -> tuple[tuple[ScoredMemory, ...], tuple[ScoredMemory, ...]]:
    """Highest score first, until the budget runs out. **Pure and deterministic.**

    A memory that does not fit is skipped and the next one is tried, rather than ending the
    packing: one long memory should not cost every shorter memory behind it. **Ties are
    broken by id** so the same inputs always produce the same prompt (snapshottable).

    `cost` takes the **record**, not its text: what reaches the prompt is a rendered line
    with the grounds spelled out (`agent.recall`), and budgeting against the bare content
    would quietly overflow by the length of those qualifiers.
    """
    ordered = sorted(scored, key=lambda item: (-item.score, item.record.id))
    selected: list[ScoredMemory] = []
    dropped: list[ScoredMemory] = []
    remaining = budget_tokens
    for item in ordered:
        spend = cost(item.record)
        if spend <= remaining:
            selected.append(item)
            remaining -= spend
        else:
            dropped.append(item)
    return tuple(selected), tuple(dropped)


class Retriever:
    """Hybrid retrieval over the memory index.

    **The clock is a parameter.** Recency and decay both depend on it, and a retriever that
    reads the wall clock cannot be tested for either.
    """

    __slots__ = ("_cost", "_embedder", "_index", "_store")

    def __init__(
        self,
        store: MemoryStore,
        index: MemoryIndex,
        embedder: EmbeddingProvider | None,
        *,
        cost: Callable[[MemoryRecord], int],
    ) -> None:
        self._store = store
        self._index = index
        #: **`None` is a supported state.** The model is fetched at runtime and the user may
        #: not have taken it; conversation continues without semantic search.
        self._embedder = embedder
        #: What a memory will cost the prompt. **Supplied by the caller**, because how a
        #: memory is rendered belongs to prompt assembly and memory does not depend on it.
        self._cost = cost

    async def retrieve(self, query: str, *, token_budget: int, now: datetime) -> RetrievalResult:
        """Memories worth putting in front of this utterance.

        **Every source fails on its own.** A broken index costs the hits it would have
        produced and nothing else: the remaining sources still run, `degraded` says so, and
        the turn continues. Failing the whole lookup because FTS5 raised would take away
        the vector results that were already in hand.
        """
        similarities: dict[str, float] = {}
        sources: dict[str, set[str]] = {}
        degraded = self._embedder is None
        embed_ms = 0.0

        if self._embedder is not None:
            started = time.perf_counter()
            try:
                vector = await self._embedder.embed_query(query)
            except Exception as error:
                # **Retrieval degrades; the turn does not fail.** Lumi answering without a
                # memory is a worse answer, and Lumi not answering is a broken product.
                log.warning("memory.embed_failed", error=str(error))
                degraded = True
            else:
                try:
                    for hit in await self._index.search(vector, VECTOR_K):
                        similarities[hit.memory_id] = hit.similarity
                        sources.setdefault(hit.memory_id, set()).add("vector")
                except Exception as error:
                    log.warning("memory.vector_search_failed", error=str(error))
                    degraded = True
            embed_ms = (time.perf_counter() - started) * 1000

        try:
            for hit in await self._index.search_keywords(query, KEYWORD_K):
                # **Keeps the larger of the two.** A keyword hit's score is a rank position,
                # not a cosine, so it must never pull a semantic match downwards.
                similarities[hit.memory_id] = max(
                    similarities.get(hit.memory_id, 0.0), hit.similarity
                )
                sources.setdefault(hit.memory_id, set()).add("keyword")
        except Exception as error:
            log.warning("memory.keyword_search_failed", error=str(error))
            degraded = True

        recent: Sequence[MemoryRecord] = ()
        try:
            recent = await self._store.recent(RECENT_K)
        except Exception as error:
            log.warning("memory.recent_failed", error=str(error))
            degraded = True
        for record in recent:
            similarities.setdefault(record.id, 0.0)
            sources.setdefault(record.id, set()).add("recent")

        if not similarities:
            return RetrievalResult(embed_ms=embed_ms, degraded=degraded)

        try:
            found = await self._store.get_many(list(similarities))
        except Exception as error:
            # **The last source standing.** Without the records themselves there is nothing
            # to score, so this one degrades to "no memories" rather than to fewer.
            log.warning("memory.read_failed", error=str(error))
            return RetrievalResult(embed_ms=embed_ms, degraded=True)
        for record in recent:
            found.setdefault(record.id, record)

        scored = [
            ScoredMemory(
                record=record,
                breakdown=score(
                    record,
                    similarities[memory_id],
                    now,
                    sources=tuple(sorted(sources.get(memory_id, ()))),
                ),
            )
            for memory_id, record in found.items()
            # A record can vanish between the index and the read (the user deleted it).
            # **The index is allowed to be stale; the answer is not.**
            if record.is_live
        ]
        selected, dropped = pack_into_budget(scored, token_budget, self._cost)
        log.info(
            "memory.retrieved",
            selected=len(selected),
            dropped=len(dropped),
            embed_ms=round(embed_ms, 1),
            degraded=degraded,
        )
        return RetrievalResult(
            selected=selected, dropped=dropped, embed_ms=embed_ms, degraded=degraded
        )

    async def record_use(self, result: RetrievalResult, *, now: datetime) -> None:
        """Count the selected memories as recalled. **Called off the turn's critical path.**

        Separate from `retrieve` so the write never delays a reply, and because being
        *considered* is not being *used*: what was dropped for budget is not reinforced.
        """
        await self._store.touch([item.record.id for item in result.selected], now=now)
