"""Retention. **docs/contracts/privacy.md §4, §5 and §7.**

privacy.md §7 says these must be testable by injecting time, and that is the whole design:
a deletion policy whose only proof is waiting 90 days is a policy nobody ever verified.

What is checked is both halves of the promise — **that expired records go**, and that
**nothing else does**. The second is the one that costs a user their memories when it is
wrong.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from lumi.memory.records import AssertionMode, MemoryCandidate, MemoryType
from lumi.memory.store import MemoryStore
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.audit import AUDIT_SCHEMA
from lumi.storage.events import EVENTS_SCHEMA
from lumi.storage.memory import MEMORY_SCHEMA, Episode, EpisodeStore, Utterance
from lumi.storage.retention import RetentionPolicy, RetentionService, Target, Trigger
from lumi.storage.sqlite import IN_MEMORY, Database, one

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


class Rig:
    """The three databases, plus the shortest way to put a dated row in each."""

    def __init__(self) -> None:
        self.memory = Database.open(IN_MEMORY, MEMORY_SCHEMA)
        self.events = Database.open(IN_MEMORY, EVENTS_SCHEMA)
        self.audit = Database.open(IN_MEMORY, AUDIT_SCHEMA)
        self.service = RetentionService(memory=self.memory, events=self.events, audit=self.audit)
        self.episodes = EpisodeStore(self.memory)
        self.memories = MemoryStore(self.memory)
        self._n = 0

    async def add_memory(
        self,
        content: str = "ユーザーは Factorio が好き",
        *,
        subject: str = "user.hobby",
        when: datetime = NOW,
    ) -> str:
        record = await self.memories.write(
            MemoryCandidate(
                type=MemoryType.SEMANTIC,
                subject=subject,
                content=content,
                assertion_mode=AssertionMode.SELF_GENERATED,
                provenance_class=ProvenanceClass.TRUSTED,
                trust_level=TrustLevel.TRUSTED,
            ),
            now=when,
        )
        return record.id

    def _next(self) -> int:
        self._n += 1
        return self._n

    async def add_episode(self, when: datetime, *, lines: int = 1) -> str:
        episode_id = f"e{self._next()}"
        await self.episodes.open_episode(Episode(id=episode_id, session_id="s", started_at=when))
        for index in range(lines):
            await self.episodes.append(
                Utterance(
                    id=f"{episode_id}-{index}",
                    episode_id=episode_id,
                    turn_index=index,
                    speaker="user",
                    text="おはよう",
                    provenance_class=ProvenanceClass.TRUSTED,
                    trust_level=TrustLevel.TRUSTED,
                    occurred_at=when,
                )
            )
        return episode_id

    def add_event(self, when: datetime) -> None:
        index = self._next()
        with self.events.transaction() as conn:
            conn.execute(
                "INSERT INTO events"
                " (id, stream_key, sequence_id, type, payload, correlation_id, occurred_at)"
                " VALUES (?, 'stream', ?, 'activity.started', '{}', 'c', ?)",
                (f"ev{index}", index, when.isoformat()),
            )

    def add_audit(self, when: datetime) -> None:
        with self.audit.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log"
                " (ts, actor, activity_id, correlation_id, capability, security_scope_json,"
                "  raw_input_digest, decision, reason, policy_version, policy_rule_id,"
                "  tool, args_digest)"
                " VALUES (?, 'user_initiated', 'a', 'c', 'character.expression', '{}',"
                "  'd', 'allow', 'r', 'v1', 'rule', 'character.set_expression', 'd')",
                (when.isoformat(),),
            )

    def counts(self) -> dict[str, int]:
        with self.memory.transaction() as conn:
            episodes = int(one(conn.execute("SELECT COUNT(*) FROM episodes"))[0])
            utterances = int(one(conn.execute("SELECT COUNT(*) FROM utterances"))[0])
        with self.events.transaction() as conn:
            events = int(one(conn.execute("SELECT COUNT(*) FROM events"))[0])
        with self.audit.transaction() as conn:
            audit = int(one(conn.execute("SELECT COUNT(*) FROM audit_log"))[0])
        return {
            "episodes": episodes,
            "utterances": utterances,
            "events": events,
            "audit": audit,
        }

    def deletion_records(self) -> list[tuple[str, int, str]]:
        with self.audit.transaction() as conn:
            rows = conn.execute(
                "SELECT target, count, trigger FROM deletion_log ORDER BY id"
            ).fetchall()
        return [(str(row[0]), int(str(row[1])), str(row[2])) for row in rows]

    def close(self) -> None:
        for database in (self.memory, self.events, self.audit):
            database.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    fixture = Rig()
    try:
        yield fixture
    finally:
        fixture.close()


# ── What expires, and what does not ──────────────────────────


async def test_each_target_expires_on_its_own_schedule(rig: Rig) -> None:
    """The three deadlines differ (90 / 30 / 180 days), and **a shared cutoff would be
    wrong for two of them.**
    """
    await rig.add_episode(days_ago(100))
    await rig.add_episode(days_ago(80))
    rig.add_event(days_ago(40))
    rig.add_event(days_ago(20))
    rig.add_audit(days_ago(200))
    rig.add_audit(days_ago(100))

    await rig.service.run(RetentionPolicy(), now=NOW)

    assert rig.counts() == {"episodes": 1, "utterances": 1, "events": 1, "audit": 1}


async def test_a_record_on_the_day_it_expires_is_kept(rig: Rig) -> None:
    """**The boundary is exclusive.** Off by a day here is a day of somebody's memories."""
    await rig.add_episode(days_ago(90) + timedelta(seconds=1))
    await rig.service.run(RetentionPolicy(), now=NOW)

    assert rig.counts()["episodes"] == 1


async def test_deleting_an_episode_takes_its_utterances(rig: Rig) -> None:
    """★ **An utterance nobody can reach is not deleted, it is hidden.**

    The row would still be in the file, still readable by anything that opened the
    database — while the user had been told the conversation was gone.
    """
    await rig.add_episode(days_ago(100), lines=3)
    await rig.add_episode(days_ago(1), lines=2)

    await rig.service.run(RetentionPolicy(), now=NOW)

    assert rig.counts() == {"episodes": 1, "utterances": 2, "events": 0, "audit": 0}


async def test_unlimited_deletes_nothing(rig: Rig) -> None:
    """**The user chose no deadline.** That has to mean exactly nothing happens."""
    await rig.add_episode(days_ago(1000))
    rig.add_event(days_ago(1000))
    rig.add_audit(days_ago(1000))

    deletions = await rig.service.run(
        RetentionPolicy(episode_days=None, audit_days=None, event_days=None), now=NOW
    )

    assert rig.counts() == {"episodes": 1, "utterances": 1, "events": 1, "audit": 1}
    assert all(deletion.count == 0 for deletion in deletions)


async def test_zero_days_deletes_everything_already_past(rig: Rig) -> None:
    """`0` is a real answer, not a synonym for unlimited. **`None` is the one that means
    "never"**, and conflating them is how "keep nothing" silently becomes "keep forever".
    """
    await rig.add_episode(days_ago(0.5))
    await rig.service.run(RetentionPolicy(episode_days=0), now=NOW)

    assert rig.counts()["episodes"] == 0


def test_a_negative_period_is_refused_when_the_policy_is_built() -> None:
    """★ **Refused at the door, not halfway through a pass.**

    A negative period is a cutoff in the future, which deletes everything. Discovering
    that inside `run` means some targets are already gone and the rest are not.
    """
    with pytest.raises(ValueError):
        RetentionPolicy(episode_days=-1)

    # The same guard still holds for a period handed straight to `cutoff`
    with pytest.raises(ValueError):
        RetentionPolicy().cutoff(-1, NOW)


async def test_a_deletion_that_could_not_be_recorded_is_still_reported(rig: Rig) -> None:
    """★ **The rows are already gone.**

    Reporting the pass as failed would leave the caller believing the records are still
    there, which is the more dangerous of the two wrong beliefs. It is logged loudly
    instead (docs/contracts/privacy.md §5).
    """
    await rig.add_episode(days_ago(100))
    with rig.audit.transaction() as conn:
        conn.execute("DROP TABLE deletion_log")

    deletions = await rig.service.run(RetentionPolicy(), now=NOW)

    assert [d.count for d in deletions if d.target is Target.EPISODES] == [1]
    assert rig.counts()["episodes"] == 0


async def test_running_twice_deletes_nothing_the_second_time(rig: Rig) -> None:
    await rig.add_episode(days_ago(100))
    first = await rig.service.run(RetentionPolicy(), now=NOW)
    second = await rig.service.run(RetentionPolicy(), now=NOW)

    assert [d.count for d in first if d.target is Target.EPISODES] == [1]
    assert [d.count for d in second if d.target is Target.EPISODES] == [0]


# ── The record of deletion ───────────────────────────────────


async def test_what_was_deleted_is_recorded(rig: Rig) -> None:
    await rig.add_episode(days_ago(100), lines=2)
    rig.add_event(days_ago(40))

    await rig.service.run(RetentionPolicy(), now=NOW)

    assert rig.deletion_records() == [
        (Target.EPISODES.value, 1, Trigger.RETENTION.value),
        (Target.EVENTS.value, 1, Trigger.RETENTION.value),
    ]


async def test_the_record_holds_no_trace_of_the_content(rig: Rig) -> None:
    """★ **A digest of a deleted utterance is still a fact about that utterance.**

    Keeping one would make "erase everything" a claim rather than a fact.
    """
    await rig.add_episode(days_ago(100))
    await rig.service.run(RetentionPolicy(), now=NOW)

    with rig.audit.transaction() as conn:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(deletion_log)").fetchall()
        }
    assert columns == {"id", "ts", "target", "count", "trigger"}


async def test_a_pass_that_removed_nothing_writes_no_record(rig: Rig) -> None:
    """**A row per target per start would bury the passes that did something.**"""
    await rig.add_episode(days_ago(1))
    await rig.service.run(RetentionPolicy(), now=NOW)

    assert rig.deletion_records() == []


async def test_the_deletion_record_outlives_the_audit_log_it_describes(rig: Rig) -> None:
    """★ **The record is not itself on a deadline** (privacy.md §2, row 10).

    If it expired with everything else, the fact that something had been removed would
    disappear along with it.
    """
    rig.add_audit(days_ago(400))
    await rig.service.run(RetentionPolicy(), now=NOW)
    assert rig.deletion_records() == [(Target.AUDIT.value, 1, Trigger.RETENTION.value)]

    # A year later, with the audit rows long gone, the record of their going is still here
    await rig.service.run(RetentionPolicy(), now=NOW + timedelta(days=365))
    assert rig.deletion_records() == [(Target.AUDIT.value, 1, Trigger.RETENTION.value)]


# ── Deleting a memory, which no deadline ever does ───────────


async def test_a_memory_record_never_expires(rig: Rig) -> None:
    """★ **privacy.md §4.** Deleting "the user likes Factorio" after 90 days is not
    forgetting, it is destruction. Forgetting is decay and archiving, and it is
    recoverable; this is not, which is why no deadline reaches it.
    """
    memory_id = await rig.add_memory(when=days_ago(4000))

    await rig.service.run(RetentionPolicy(episode_days=1, event_days=1, audit_days=1), now=NOW)

    assert await rig.memories.get(memory_id) is not None


async def test_deleting_the_conversation_does_not_delete_what_was_learned(rig: Rig) -> None:
    """★ The Episode expires; the belief does not. **The evidence reference is deliberately
    not a foreign key** — with one, retention would either fail to delete the episode or
    take the belief with it.
    """
    episode_id = await rig.add_episode(days_ago(100))
    record = await rig.memories.write(
        MemoryCandidate(
            type=MemoryType.SEMANTIC,
            subject="user.hobby",
            content="ユーザーは Factorio が好き",
            assertion_mode=AssertionMode.USER_STATED,
            provenance_class=ProvenanceClass.TRUSTED,
            trust_level=TrustLevel.TRUSTED,
            evidence_ref=(f"{episode_id}-0",),
        ),
        now=days_ago(100),
    )

    await rig.service.run(RetentionPolicy(), now=NOW)

    assert rig.counts()["episodes"] == 0
    kept = await rig.memories.get(record.id)
    assert kept is not None
    assert kept.evidence_ref == (f"{episode_id}-0",)


async def test_the_user_can_delete_a_memory_outright(rig: Rig) -> None:
    """ "個別の削除" (privacy.md §5). `archive()` is "forget this"; **this is "that should
    never have been written down"**, and it does not come back.
    """
    doomed = await rig.add_memory("ユーザーは火星人")
    kept = await rig.add_memory("ユーザーは猫を飼っている", subject="user.pet")

    deletion = await rig.service.purge_memories((doomed,), now=NOW)

    assert deletion.count == 1
    assert await rig.memories.get(doomed) is None
    assert await rig.memories.get(kept) is not None


async def test_deleting_a_memory_is_recorded_without_its_content(rig: Rig) -> None:
    """The record says a memory went. **A digest of it would still be a fact about it**,
    and would make "erase everything" a lie.
    """
    memory_id = await rig.add_memory("ユーザーは火星人")

    await rig.service.purge_memories((memory_id,), now=NOW)

    assert rig.deletion_records() == [(Target.MEMORIES.value, 1, Trigger.PURGE.value)]
    with rig.audit.transaction() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(deletion_log)").fetchall()]
    assert "content" not in columns and "digest" not in columns


async def test_deleting_a_superseded_belief_does_not_take_its_successor(rig: Rig) -> None:
    """★ A record whose predecessor the user deleted is still a belief they hold.

    Following `superseded_by` would turn "delete this one" into "delete the history it
    happens to be part of" — and the successor is the **current** belief.
    """
    old = await rig.add_memory("ユーザーは Factorio が好き")
    outcome = await rig.memories.supersede(
        old,
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

    await rig.service.purge_memories((old,), now=NOW)

    successor = await rig.memories.get(outcome.record.id)
    assert successor is not None
    assert successor.is_live


async def test_deleting_a_belief_takes_its_evidence_rows(rig: Rig) -> None:
    """Rows nobody can reach are still rows on disk — deleted from the user's point of
    view, present in fact. Written out rather than left to `ON DELETE CASCADE`, which
    needs a pragma that can silently fail to apply.
    """
    episode_id = await rig.add_episode(days_ago(1))
    record = await rig.memories.write(
        MemoryCandidate(
            type=MemoryType.SEMANTIC,
            subject="user.hobby",
            content="ユーザーは Factorio が好き",
            assertion_mode=AssertionMode.USER_STATED,
            provenance_class=ProvenanceClass.TRUSTED,
            trust_level=TrustLevel.TRUSTED,
            evidence_ref=(f"{episode_id}-0",),
            source_episode_ids=(episode_id,),
        ),
        now=NOW,
    )

    await rig.service.purge_memories((record.id,), now=NOW)

    with rig.memory.transaction() as conn:
        evidence = int(one(conn.execute("SELECT COUNT(*) FROM memory_evidence"))[0])
        sources = int(one(conn.execute("SELECT COUNT(*) FROM memory_sources"))[0])
    assert (evidence, sources) == (0, 0)


async def test_deleting_nothing_writes_no_record(rig: Rig) -> None:
    deletion = await rig.service.purge_memories((), now=NOW)

    assert deletion.count == 0
    assert rig.deletion_records() == []


async def test_deleting_the_current_belief_leaves_its_predecessor_readable(rig: Rig) -> None:
    """★ The other direction of the same rule.

    Purging the successor must not leave the older row pointing at an id that is gone:
    a `superseded_by` naming nothing would be a belief that is neither live nor
    superseded by anything — **invisible to retrieval, with no successor to find instead.**
    """
    old = await rig.add_memory("ユーザーは Factorio が好き")
    outcome = await rig.memories.supersede(
        old,
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

    await rig.service.purge_memories((outcome.record.id,), now=NOW)

    predecessor = await rig.memories.get(old)
    assert predecessor is not None
    assert predecessor.superseded_by is None
    assert predecessor.is_live
