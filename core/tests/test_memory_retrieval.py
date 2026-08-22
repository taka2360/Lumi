"""Remembering. **docs/architecture/memory.md §7 / docs/interfaces/memory.md tests 2, 7, 8.**

The scoring here decides what Lumi appears to know, so the parts that must not drift are:

* **the cut is deterministic** — the same memories and the same budget always produce the
  same prompt, and what did not fit comes back as `dropped` rather than vanishing
* **grounds multiply, they do not add** — a confident guess about nothing must not outrank
  the thing that was actually asked about
* **archived and superseded memories do not come back** — otherwise the forgetting curve
  is decoration

No LLM and no ONNX session is involved: the embedder is a fake, because **what is being
tested is the arithmetic and the wiring, not the model** (.claude/rules/tests.md).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from lumi.agent.recall import cost_of
from lumi.memory.decay import FLOOR
from lumi.memory.indexing import Indexer
from lumi.memory.records import AssertionMode, MemoryCandidate, MemoryRecord, MemoryType
from lumi.memory.retrieval import (
    RECENT_K,
    Retriever,
    ScoredMemory,
    pack_into_budget,
    recency,
    score,
)
from lumi.memory.store import MemoryStore
from lumi.memory.vectors import MemoryIndex, document_text, keywords
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.memory import EMBEDDING_DIMENSION, open_memory
from lumi.storage.sqlite import IN_MEMORY

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class FakeEmbedder:
    """Deterministic vectors from the characters in the text. **No model, no ONNX.**

    Each distinct character claims one dimension, so two texts sharing characters point in
    similar directions. Characters rather than words because Japanese does not put spaces
    between them — and it is not a language model either way: it exists to prove the right
    text reached the right side of the call.
    """

    id = "fake"

    def __init__(self, *, model: str = "fake@v1") -> None:
        self.model = model
        self.queries: list[str] = []
        self.documents: list[str] = []

    def dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def model_id(self) -> str:
        return self.model

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        for character in text:
            if not character.isspace():
                vector[ord(character) % EMBEDDING_DIMENSION] += 1.0
        norm = float(np.linalg.norm(vector))
        return vector if norm == 0 else vector / norm

    async def embed_query(self, text: str) -> np.ndarray:
        self.queries.append(text)
        return self._vector(text)

    async def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        self.documents.extend(texts)
        return [self._vector(text) for text in texts]


class Rig:
    def __init__(self) -> None:
        self.db = open_memory(IN_MEMORY)
        self.store = MemoryStore(self.db)
        self.index = MemoryIndex(self.db)
        self.embedder = FakeEmbedder()
        self.retriever = Retriever(
            self.store, self.index, self.embedder, cost=lambda record: len(record.content)
        )

    async def remember(
        self,
        content: str,
        *,
        subject: str = "user.hobby",
        mode: AssertionMode = AssertionMode.SELF_GENERATED,
        salience: float = 0.6,
        when: datetime = NOW,
        kind: MemoryType = MemoryType.SEMANTIC,
    ) -> MemoryRecord:
        """A belief, written straight in.

        **`self_generated` by default** so these can be written without inventing
        utterances to cite — `user_stated` must name evidence that exists, which is a
        property of the store and is tested there rather than re-tested through every
        retrieval case.
        """
        return await self.store.write(
            MemoryCandidate(
                type=kind,
                subject=subject,
                content=content,
                assertion_mode=mode,
                provenance_class=ProvenanceClass.TRUSTED,
                trust_level=TrustLevel.TRUSTED,
                base_salience=salience,
                valid_from=when,
            ),
            now=when,
        )

    async def index_everything(self) -> None:
        await Indexer(self.store, self.index, self.embedder).run_until_done()

    def close(self) -> None:
        self.db.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    fixture = Rig()
    try:
        yield fixture
    finally:
        fixture.close()


def memory(
    *,
    content: str = "ユーザーは Factorio が好き",
    mode: AssertionMode = AssertionMode.USER_STATED,
    salience: float = 0.5,
    valid_from: datetime = NOW,
    identifier: str = "m1",
) -> MemoryRecord:
    return MemoryRecord(
        id=identifier,
        type=MemoryType.SEMANTIC,
        subject="user.hobby",
        content=content,
        assertion_mode=mode,
        evidence_ref=(),
        confidence=0.8,
        provenance_class=ProvenanceClass.TRUSTED,
        trust_level=TrustLevel.TRUSTED,
        base_salience=salience,
        created_at=valid_from,
        last_accessed=valid_from,
        access_count=0,
        archived_at=None,
        valid_from=valid_from,
        superseded_by=None,
    )


# ── Scoring, as arithmetic ───────────────────────────────────


def test_recency_is_measured_from_when_the_belief_started() -> None:
    """★ Not from when the row was written. **A memory recorded today about something the
    user said last year is not news**, and `valid_from` is the field that knows.
    """
    fresh = recency(memory(valid_from=NOW), NOW)
    old = recency(memory(valid_from=NOW - timedelta(days=180)), NOW)

    assert fresh == pytest.approx(1.0)
    assert old < 0.01


def test_grounds_multiply_rather_than_add() -> None:
    """★ As an addend, a `user_confirmed` memory with similarity 0 would outrank a
    `user_stated` one that actually matches. **The weight scales relevance; it is not
    relevance of its own.**
    """
    irrelevant = score(memory(mode=AssertionMode.USER_CONFIRMED), 0.0, NOW)
    relevant = score(memory(mode=AssertionMode.USER_STATED), 0.9, NOW)

    assert relevant.total > irrelevant.total
    assert irrelevant.assertion_weight == 1.2


def test_the_same_memory_scores_higher_when_better_grounded() -> None:
    stated = score(memory(mode=AssertionMode.USER_STATED), 0.7, NOW)
    guessed = score(memory(mode=AssertionMode.SELF_GENERATED), 0.7, NOW)

    assert stated.total > guessed.total


def test_a_faded_memory_scores_below_a_vivid_one() -> None:
    vivid = score(memory(salience=0.9), 0.5, NOW)
    faded = score(memory(salience=FLOOR), 0.5, NOW)

    assert vivid.total > faded.total
    assert vivid.effective_salience > faded.effective_salience


def test_every_term_is_reported() -> None:
    """`dropped` and `breakdown` exist so the Inspector can answer **"why was this not
    remembered"** — impossible from a single number.
    """
    breakdown = score(memory(), 0.5, NOW, sources=("vector", "keyword"))

    assert breakdown.similarity == 0.5
    assert 0 < breakdown.recency <= 1
    assert breakdown.sources == ("vector", "keyword")


# ── The budget ───────────────────────────────────────────────


def test_packing_takes_the_best_and_reports_the_rest() -> None:
    """★ Test 7. **The LLM is never asked what to cut.**"""
    items = [
        ScoredMemory(memory(identifier="a", content="x" * 10), score(memory(), 0.9, NOW)),
        ScoredMemory(memory(identifier="b", content="y" * 10), score(memory(), 0.1, NOW)),
    ]

    selected, dropped = pack_into_budget(items, 10, lambda record: len(record.content))

    assert [item.record.id for item in selected] == ["a"]
    assert [item.record.id for item in dropped] == ["b"]


def test_one_oversized_memory_does_not_block_the_rest() -> None:
    """Stopping at the first thing that does not fit would let a single long memory cost
    every shorter one behind it.
    """
    huge = ScoredMemory(memory(identifier="a", content="x" * 100), score(memory(), 0.9, NOW))
    small = ScoredMemory(memory(identifier="b", content="y"), score(memory(), 0.8, NOW))

    selected, dropped = pack_into_budget([huge, small], 10, lambda record: len(record.content))

    assert [item.record.id for item in selected] == ["b"]
    assert [item.record.id for item in dropped] == ["a"]


def test_packing_is_deterministic_down_to_the_tie() -> None:
    """★ The same input must produce the same prompt — otherwise nothing here is
    snapshottable and every prompt test is flaky.
    """
    tied = [
        ScoredMemory(memory(identifier=name, content="x"), score(memory(), 0.5, NOW))
        for name in ("b", "a", "c")
    ]

    first, _ = pack_into_budget(tied, 2, lambda record: len(record.content))
    again, _ = pack_into_budget(list(reversed(tied)), 2, lambda record: len(record.content))

    assert [item.record.id for item in first] == ["a", "b"]
    assert [item.record.id for item in again] == ["a", "b"]


def test_nothing_fits_in_no_budget() -> None:
    items = [ScoredMemory(memory(content="x"), score(memory(), 0.9, NOW))]

    selected, dropped = pack_into_budget(items, 0, lambda record: len(record.content))

    assert selected == ()
    assert len(dropped) == 1


# ── The index ────────────────────────────────────────────────


async def test_a_memory_is_found_by_what_it_is_about(rig: Rig) -> None:
    cat = await rig.remember("ユーザーは猫を飼っている", subject="user.pet")
    await rig.remember("ユーザーは Rust を書いている", subject="user.work")
    await rig.index_everything()

    result = await rig.retriever.retrieve("猫 の はなし", token_budget=1000, now=NOW)

    assert result.selected[0].record.id == cat.id
    assert "vector" in result.selected[0].breakdown.sources


async def test_documents_are_indexed_with_their_subject(rig: Rig) -> None:
    """★ Measured: recall@3 went 90% → 100% with the subject prefixed (ADR-041). **The
    text indexed has to be the text embedded**, or search is scoring a different string
    than the one it stored.
    """
    record = await rig.remember("ユーザーは猫を飼っている", subject="user.pet")
    await rig.index_everything()

    assert rig.embedder.documents == [document_text(record)]
    assert rig.embedder.documents[0].startswith("user.pet: ")


async def test_the_query_goes_through_the_query_path(rig: Rig) -> None:
    """★ The other half of ADR-041's asymmetry, checked where it is actually used."""
    await rig.remember("ユーザーは猫を飼っている")
    await rig.index_everything()

    await rig.retriever.retrieve("猫は元気?", token_budget=1000, now=NOW)

    assert rig.embedder.queries == ["猫は元気?"]


async def test_an_archived_memory_is_not_retrieved(rig: Rig) -> None:
    """★ Test 2. A memory below the floor is one Lumi **cannot currently recall**; if
    search returned it anyway, decay would be decoration.
    """
    faded = await rig.remember("ユーザーは猫を飼っている", subject="user.pet")
    await rig.index_everything()
    await rig.store.archive(faded.id, now=NOW)

    result = await rig.retriever.retrieve("猫", token_budget=1000, now=NOW)

    assert faded.id not in {item.record.id for item in result.selected}


async def test_a_superseded_belief_is_not_retrieved(rig: Rig) -> None:
    """It is still on disk and still readable — **it is just not what Lumi believes now.**"""
    old = await rig.remember("ユーザーは Factorio が好き")
    await rig.index_everything()
    await rig.store.supersede(
        old.id,
        MemoryCandidate(
            type=MemoryType.SEMANTIC,
            subject="user.hobby",
            content="最近は Rimworld をやっている",
            assertion_mode=AssertionMode.SELF_GENERATED,
            provenance_class=ProvenanceClass.TRUSTED,
            trust_level=TrustLevel.TRUSTED,
        ),
        now=NOW,
    )
    await rig.index_everything()

    result = await rig.retriever.retrieve("Factorio の話", token_budget=1000, now=NOW)

    assert old.id not in {item.record.id for item in result.selected}


async def test_removing_from_the_index_keeps_the_belief(rig: Rig) -> None:
    """**The index is not the memory.** Dropping a row here loses findability, not a
    belief — which is why these are the tables that may be rewritten freely.
    """
    record = await rig.remember("ユーザーは猫を飼っている")
    await rig.index_everything()

    await rig.index.remove([record.id])

    assert await rig.index.count() == 0
    assert await rig.store.get(record.id) is not None


# ── Keyword search, and what it cannot do ────────────────────


def test_short_terms_are_never_sent_to_the_trigram_index() -> None:
    """★ FTS5's trigram tokenizer **cannot match fewer than three characters**. 「猫」 finds
    nothing, and asking anyway spends a query to discover it. This is why keyword search is
    the second opinion and the vector search is the first.
    """
    assert keywords("猫は元気?") == ["猫は元気"]
    assert keywords("猫") == []
    assert keywords("ミケ は 元気") == []


async def test_a_name_is_found_by_keyword_even_without_similarity(rig: Rig) -> None:
    record = await rig.remember("ユーザーは AivisSpeech を使っている", subject="user.tools")
    await rig.index_everything()

    hits = await rig.index.search_keywords("AivisSpeech の設定", 5)

    assert [hit.memory_id for hit in hits] == [record.id]


async def test_fts_query_syntax_in_speech_is_not_a_query(rig: Rig) -> None:
    """★ What the user said is **data, not FTS5 syntax** (Invariant 3 in miniature).
    Unquoted, a stray `OR` or `*` would change the query or raise from inside SQLite.
    """
    await rig.remember("ユーザーは Factorio が好き")
    await rig.index_everything()

    hits = await rig.index.search_keywords('Factorio OR "" * AND', 5)

    assert isinstance(hits, list)


# ── Degrading rather than failing ────────────────────────────


async def test_retrieval_without_an_embedder_still_returns_memories(rig: Rig) -> None:
    """★ The model is fetched at runtime and the user may not have taken it. **Lumi
    answering without a memory is a worse answer; Lumi not answering is a broken product.**
    """
    await rig.remember("ユーザーは猫を飼っている")
    retriever = Retriever(rig.store, rig.index, None, cost=lambda record: len(record.content))

    result = await retriever.retrieve("猫は元気?", token_budget=1000, now=NOW)

    assert result.degraded
    assert len(result.selected) == 1


async def test_a_failing_embedder_degrades_the_search_not_the_turn(rig: Rig) -> None:
    class Broken(FakeEmbedder):
        async def embed_query(self, text: str) -> np.ndarray:
            raise RuntimeError("session died")

    await rig.remember("ユーザーは猫を飼っている")
    retriever = Retriever(rig.store, rig.index, Broken(), cost=lambda record: len(record.content))

    result = await retriever.retrieve("猫は元気?", token_budget=1000, now=NOW)

    assert result.degraded
    assert len(result.selected) == 1


async def test_the_newest_memories_are_candidates_even_before_indexing(rig: Rig) -> None:
    """A memory written a minute ago has no vector yet. **Without the recency source it
    would be invisible until the next index pass**, which is exactly when a user is most
    likely to refer to it.
    """
    record = await rig.remember("さっき引っ越しの話をした", subject="episode.moving")

    result = await rig.retriever.retrieve("引っ越し", token_budget=1000, now=NOW)

    selected = {item.record.id for item in result.selected}
    assert record.id in selected
    assert await rig.index.count() == 0


async def test_only_a_few_recent_memories_are_pulled_in(rig: Rig) -> None:
    """Otherwise every retrieval quietly becomes "the last N memories", and similarity
    stops deciding anything.
    """
    for index in range(RECENT_K + 5):
        await rig.remember(f"memory {index}", subject=f"user.thing{index}")

    result = await rig.retriever.retrieve("まったく無関係な質問", token_budget=1000, now=NOW)

    assert len(result.selected) <= RECENT_K


# ── Being recalled is recorded, separately ───────────────────


async def test_recall_is_recorded_only_for_what_was_used(rig: Rig) -> None:
    """★ Being *considered* is not being *used*. What the budget dropped is not reinforced
    — otherwise `access_boost` rewards memories that never reached the prompt.
    """
    kept = await rig.remember("猫の話", subject="user.pet", salience=0.9)
    cut = await rig.remember("まったく別の話", subject="user.other", salience=0.1)
    await rig.index_everything()

    result = await rig.retriever.retrieve("猫の話", token_budget=3, now=NOW)
    await rig.retriever.record_use(result, now=NOW + timedelta(minutes=1))

    assert [item.record.id for item in result.selected] == [kept.id]
    refreshed = await rig.store.get(kept.id)
    other = await rig.store.get(cut.id)
    assert refreshed is not None and refreshed.access_count == 1
    assert other is not None and other.access_count == 0


# ── Re-embedding after a model change ────────────────────────


async def test_a_new_memory_is_indexed_once(rig: Rig) -> None:
    await rig.remember("ユーザーは猫を飼っている")

    first = await Indexer(rig.store, rig.index, rig.embedder).run()
    again = await Indexer(rig.store, rig.index, rig.embedder).run()

    assert first.embedded == 1
    assert again.embedded == 0


async def test_changing_the_model_re_embeds_everything(rig: Rig) -> None:
    """★ Vectors from two models are not comparable. **The mismatch is what makes a swap
    detectable** instead of a slow, unexplained decline in search quality.
    """
    await rig.remember("ユーザーは猫を飼っている")
    await rig.index_everything()

    rig.embedder.model = "fake@v2"
    report = await Indexer(rig.store, rig.index, rig.embedder).run()

    assert report.embedded == 1


async def test_indexing_is_bounded_per_pass(rig: Rig) -> None:
    """Embedding 500 memories in one go holds a thread for ten seconds, and **what shares
    that CPU is capture, VAD and barge-in.**
    """
    for index in range(5):
        await rig.remember(f"memory {index}", subject=f"user.thing{index}")

    report = await Indexer(rig.store, rig.index, rig.embedder, limit=2).run()

    assert report.embedded == 2
    assert report.remaining


async def test_the_mark_is_only_written_for_what_was_stored(rig: Rig) -> None:
    """If the index write fails, nothing is marked — the record simply looks unembedded and
    is retried. **The other order leaves a record claiming a vector that is not there.**
    """

    class BrokenIndex(MemoryIndex):
        async def upsert(self, entries: object) -> None:
            raise RuntimeError("disk is on fire")

    await rig.remember("ユーザーは猫を飼っている")
    indexer = Indexer(rig.store, BrokenIndex(rig.db), rig.embedder)

    with pytest.raises(RuntimeError):
        await indexer.run()

    pending = await rig.store.needing_embedding(rig.embedder.model_id(), limit=10)
    assert len(pending) == 1


# ── One broken source does not take the others down ──────────


async def test_a_broken_keyword_index_still_leaves_the_vector_hits(rig: Rig) -> None:
    """★ Failing the whole lookup because FTS5 raised would throw away vector results that
    were **already in hand**. The turn gets fewer memories and is told the search was
    degraded; it does not get an exception.
    """
    record = await rig.remember("ユーザーは猫を飼っている", subject="user.pet")
    await rig.index_everything()

    class NoKeywords(MemoryIndex):
        async def search_keywords(self, text: str, k: int) -> list[Any]:
            raise RuntimeError("fts5 is unhappy")

    retriever = Retriever(
        rig.store, NoKeywords(rig.db), rig.embedder, cost=lambda record: len(record.content)
    )

    result = await retriever.retrieve("猫の話", token_budget=1000, now=NOW)

    assert result.degraded
    assert record.id in {item.record.id for item in result.selected}


async def test_a_broken_vector_index_still_leaves_the_keyword_hits(rig: Rig) -> None:
    record = await rig.remember("ユーザーは AivisSpeech を使っている", subject="user.tools")
    await rig.index_everything()

    class NoVectors(MemoryIndex):
        async def search(self, query: Any, k: int) -> list[Any]:
            raise RuntimeError("vec0 is unhappy")

    retriever = Retriever(
        rig.store, NoVectors(rig.db), rig.embedder, cost=lambda record: len(record.content)
    )

    result = await retriever.retrieve("AivisSpeech の設定", token_budget=1000, now=NOW)

    assert result.degraded
    assert record.id in {item.record.id for item in result.selected}


async def test_a_database_that_cannot_be_read_returns_nothing_rather_than_raising(
    rig: Rig,
) -> None:
    """The one source that cannot degrade to "fewer": without the records there is nothing
    to score. **It still does not raise** — the turn goes on without memories.
    """
    await rig.remember("ユーザーは猫を飼っている")
    await rig.index_everything()

    class Unreadable(MemoryStore):
        async def get_many(self, memory_ids: Any) -> dict[str, Any]:
            raise RuntimeError("the database is on fire")

    retriever = Retriever(
        Unreadable(rig.db), rig.index, rig.embedder, cost=lambda record: len(record.content)
    )

    result = await retriever.retrieve("猫", token_budget=1000, now=NOW)

    assert result.degraded
    assert result.selected == ()


# ── The budget counts what the prompt will actually hold ─────


async def test_the_budget_counts_the_rendered_line(rig: Rig) -> None:
    """★ A memory reaches the prompt as a rendered line with its grounds spelled out
    (`agent.recall`). Budgeting on the bare content **overflows by exactly the length of
    the qualifiers** — which are longest on the memories Lumi should be most careful with.
    """
    guess = await rig.remember("ユーザーは猫を飼っている", mode=AssertionMode.SELF_GENERATED)

    bare = len(guess.content)
    rendered = cost_of(guess, len)

    assert rendered > bare
    retriever = Retriever(
        rig.store, rig.index, rig.embedder, cost=lambda record: cost_of(record, len)
    )
    result = await retriever.retrieve("猫", token_budget=bare, now=NOW)

    assert result.selected == ()
    assert [item.record.id for item in result.dropped] == [guess.id]
