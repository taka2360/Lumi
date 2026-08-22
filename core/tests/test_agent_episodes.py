"""Recording the conversation from the loop's side.

The properties here are the ones that decide whether the log is trustworthy: **it opens
only when something was actually said**, it keeps the order it was said in even though
the writes are handed to tasks, and **a database that will not take a write costs the log
rather than the reply.**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lumi.agent.episodes import EpisodeRecorder
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.memory import MEMORY_SCHEMA, SPEAKER_LUMI, SPEAKER_USER, Episode, EpisodeStore
from lumi.storage.sqlite import IN_MEMORY, Database

START = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


@pytest.fixture
def store() -> EpisodeStore:
    return EpisodeStore(Database.open(IN_MEMORY, MEMORY_SCHEMA))


async def test_a_turn_is_written_down(store: EpisodeStore) -> None:
    recorder = EpisodeRecorder(store, session_id="s1")
    recorder.remember_user("おはよう")
    recorder.remember_lumi("おはよう。", TrustLevel.TRUSTED)
    await recorder.flush()

    episode_id = recorder.episode_id
    assert episode_id is not None
    lines = await store.utterances(episode_id)
    assert [(line.speaker, line.text) for line in lines] == [
        (SPEAKER_USER, "おはよう"),
        (SPEAKER_LUMI, "おはよう。"),
    ]


async def test_nothing_said_leaves_nothing_behind(store: EpisodeStore) -> None:
    """**An empty episode is a record of a conversation that did not happen.**"""
    recorder = EpisodeRecorder(store, session_id="s1")
    await recorder.close()

    assert recorder.episode_id is None


async def test_an_empty_utterance_is_not_recorded(store: EpisodeStore) -> None:
    """A turn cut off before it said anything has no text. **Not a blank line in the log.**"""
    recorder = EpisodeRecorder(store, session_id="s1")
    recorder.remember_lumi("", TrustLevel.TRUSTED)
    await recorder.flush()

    assert recorder.episode_id is None


async def test_the_order_survives_the_writes_landing_out_of_order(store: EpisodeStore) -> None:
    """★ Writes go to tasks so the turn is not waiting on a disk. **The index is assigned
    when the turn happens**, which is what keeps the log in the order it was spoken.
    """
    recorder = EpisodeRecorder(store, session_id="s1")
    for index in range(6):
        recorder.remember_user(f"line {index}")
    await recorder.flush()

    episode_id = recorder.episode_id
    assert episode_id is not None
    lines = await store.utterances(episode_id)
    assert [line.text for line in lines] == [f"line {index}" for index in range(6)]
    assert [line.turn_index for line in lines] == list(range(6))


async def test_lumi_keeps_the_trust_it_was_given(store: EpisodeStore) -> None:
    """**No automatic step may raise trust** (Invariant 7), and none may lower the record
    of it either: what is written is what the turn actually carried.
    """
    recorder = EpisodeRecorder(store, session_id="s1")
    recorder.remember_lumi("ページによると…", TrustLevel.TAINTED)
    await recorder.flush()

    episode_id = recorder.episode_id
    assert episode_id is not None
    line = (await store.utterances(episode_id))[0]
    assert line.trust_level is TrustLevel.TAINTED
    assert line.provenance_class is ProvenanceClass.DERIVED


async def test_the_user_is_recorded_as_trusted_input(store: EpisodeStore) -> None:
    recorder = EpisodeRecorder(store, session_id="s1")
    recorder.remember_user("おはよう")
    await recorder.flush()

    episode_id = recorder.episode_id
    assert episode_id is not None
    line = (await store.utterances(episode_id))[0]
    assert line.trust_level is TrustLevel.TRUSTED
    assert line.provenance_class is ProvenanceClass.TRUSTED


async def test_closing_stamps_the_end(store: EpisodeStore) -> None:
    clock = Clock()
    recorder = EpisodeRecorder(store, session_id="s1", clock=clock)
    recorder.remember_user("おはよう")
    clock.advance(30)
    await recorder.close()

    episode_id = recorder.episode_id
    assert episode_id is not None
    episode = await store.episode(episode_id)
    assert episode is not None
    assert episode.started_at == START
    assert episode.ended_at == START + timedelta(minutes=30)


async def test_a_failing_database_does_not_break_the_turn(store: EpisodeStore) -> None:
    """★ **The conversation is not cancelled because the filing cabinet is stuck.**

    Failing to record is a broken log; failing to answer is a broken Lumi.
    """

    class Broken(EpisodeStore):
        async def open_episode(self, episode: Episode) -> None:
            raise RuntimeError("disk is on fire")

    recorder = EpisodeRecorder(Broken(Database.open(IN_MEMORY, MEMORY_SCHEMA)), session_id="s1")
    recorder.remember_user("おはよう")

    await recorder.flush()  # **raises nothing**
    await recorder.close()


async def test_flush_waits_for_what_was_started(store: EpisodeStore) -> None:
    """Without it, a test — or the shutdown path — reads a log that has not landed yet."""
    recorder = EpisodeRecorder(store, session_id="s1")
    recorder.remember_user("おはよう")

    episode_id = recorder.episode_id
    assert episode_id is not None
    await recorder.flush()
    assert len(await store.utterances(episode_id)) == 1
