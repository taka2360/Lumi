"""The conversation log. **docs/contracts/privacy.md §2 (row 1) / architecture/memory.md.**

Phase 2 is the first time anything the user said survives the process, so what is checked
here is not "does the INSERT work" but the properties the privacy contract promises about
it: the text is readable back exactly, **trust travels with it**, and the order is the
order it was said in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.memory import (
    MEMORY_SCHEMA,
    SPEAKER_LUMI,
    SPEAKER_USER,
    Episode,
    EpisodeStore,
    Utterance,
)
from lumi.storage.sqlite import IN_MEMORY, Database

KEY = "ab" * 32


def at(minute: int) -> datetime:
    return datetime(2026, 8, 22, 12, minute, tzinfo=UTC)


@pytest.fixture
def store() -> EpisodeStore:
    return EpisodeStore(Database.open(IN_MEMORY, MEMORY_SCHEMA))


def utterance(
    episode_id: str,
    index: int,
    text: str,
    *,
    speaker: str = SPEAKER_USER,
    trust: TrustLevel = TrustLevel.TRUSTED,
) -> Utterance:
    return Utterance(
        id=f"u{index}",
        episode_id=episode_id,
        turn_index=index,
        speaker=speaker,
        text=text,
        provenance_class=(
            ProvenanceClass.TRUSTED if trust is TrustLevel.TRUSTED else ProvenanceClass.DERIVED
        ),
        trust_level=trust,
        occurred_at=at(index),
    )


async def test_a_conversation_reads_back_as_it_was_said(store: EpisodeStore) -> None:
    await store.open_episode(Episode(id="e1", session_id="s1", started_at=at(0)))
    await store.append(utterance("e1", 0, "おはよう"))
    await store.append(utterance("e1", 1, "おはよう。よく眠れた？", speaker=SPEAKER_LUMI))

    lines = await store.utterances("e1")
    assert [(line.speaker, line.text) for line in lines] == [
        (SPEAKER_USER, "おはよう"),
        (SPEAKER_LUMI, "おはよう。よく眠れた？"),
    ]


async def test_order_comes_from_the_turn_index_not_the_write_order(store: EpisodeStore) -> None:
    """★ Writes are handed to tasks and **may land out of order** (`agent/episodes.py`).

    The index is assigned when the turn happens, so the log still reads back as spoken.
    """
    await store.open_episode(Episode(id="e1", session_id="s1", started_at=at(0)))
    await store.append(utterance("e1", 2, "three"))
    await store.append(utterance("e1", 0, "one"))
    await store.append(utterance("e1", 1, "two"))

    assert [line.text for line in await store.utterances("e1")] == ["one", "two", "three"]


async def test_trust_is_stored_rather_than_recomputed(store: EpisodeStore) -> None:
    """**No automatic step may raise trust** (Invariant 7).

    A reply shaped by an untrusted tool result is tainted, and nothing reading this log a
    week later could work that out again — so it is written down, not derived.
    """
    await store.open_episode(Episode(id="e1", session_id="s1", started_at=at(0)))
    await store.append(
        utterance("e1", 0, "まとめると…", speaker=SPEAKER_LUMI, trust=TrustLevel.TAINTED)
    )

    stored = (await store.utterances("e1"))[0]
    assert stored.trust_level is TrustLevel.TAINTED
    assert stored.provenance_class is ProvenanceClass.DERIVED


async def test_the_same_turn_cannot_be_written_twice(store: EpisodeStore) -> None:
    """**A retry must not duplicate a line.** Two rows at one index is a conversation that
    reads as though something was said twice.
    """
    await store.open_episode(Episode(id="e1", session_id="s1", started_at=at(0)))
    await store.append(utterance("e1", 0, "おはよう"))

    with pytest.raises(Exception, match="UNIQUE"):
        await store.append(utterance("e1", 0, "おはよう"))


async def test_an_utterance_needs_an_episode(store: EpisodeStore) -> None:
    """**Fails rather than filing it under a conversation that does not exist.**"""
    with pytest.raises(Exception, match="FOREIGN KEY"):
        await store.append(utterance("missing", 0, "誰もいない"))


async def test_closing_marks_the_end(store: EpisodeStore) -> None:
    await store.open_episode(Episode(id="e1", session_id="s1", started_at=at(0)))
    assert (await store.episode("e1")).ended_at is None  # type: ignore[union-attr]

    await store.close_episode("e1", at(30))
    # **Closing twice does not move the end.** The first close is when it actually ended;
    # a later one would be a record of when someone happened to call the function again
    await store.close_episode("e1", at(45))

    closed = await store.episode("e1")
    assert closed is not None
    assert closed.ended_at == at(30)


async def test_closing_an_episode_that_was_never_opened_is_not_an_error(
    store: EpisodeStore,
) -> None:
    """A session where nobody spoke never opened one. **Not a failure to report.**"""
    await store.close_episode("never-existed", at(1))


async def test_the_log_survives_a_restart(tmp_path: Path) -> None:
    """**This is the whole point of Phase 2.** Up to Phase 1 it did not."""
    path = tmp_path / "memory.db"
    first = Database.open(path, MEMORY_SCHEMA, key=KEY)
    store = EpisodeStore(first)
    await store.open_episode(Episode(id="e1", session_id="s1", started_at=at(0)))
    await store.append(utterance("e1", 0, "Factorio が好き"))
    first.close()

    second = Database.open(path, MEMORY_SCHEMA, key=KEY)
    try:
        reopened = EpisodeStore(second)
        assert [line.text for line in await reopened.utterances("e1")] == ["Factorio が好き"]
    finally:
        second.close()
