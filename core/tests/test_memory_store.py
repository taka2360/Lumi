"""What Lumi is willing to believe, and what it refuses to.

**docs/architecture/memory.md §10 (tests 2-9) / docs/interfaces/memory.md (tests 2-6).**

Two properties here are not features and cannot be traded away:

* **`confirm()` is the only way a memory becomes `TRUSTED`** (Invariant 7). Everything
  else propagates trust from the utterances the belief was made of, and propagation can
  only move toward taint.
* **Nothing in the store deletes.** `archive()` is an `UPDATE`; the belief is still there
  afterwards, which is what makes "思い出せなくなる" different from "無かったことになる".
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lumi.memory.contradiction import Resolution
from lumi.memory.decay import FLOOR
from lumi.memory.records import AssertionMode, MemoryCandidate, MemoryType
from lumi.memory.store import MemoryRejected, MemoryStore
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.memory import MEMORY_SCHEMA, Episode, EpisodeStore, Utterance
from lumi.storage.sqlite import IN_MEMORY, Database, Schema, one

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
CORE = Path(__file__).resolve().parents[1]


class Rig:
    """A memory database with a conversation already in it to cite."""

    def __init__(self) -> None:
        self.db = Database.open(IN_MEMORY, MEMORY_SCHEMA)
        self.store = MemoryStore(self.db)
        self.episodes = EpisodeStore(self.db)

    async def say(
        self,
        utterance_id: str,
        text: str = "Factorio 面白いよ",
        *,
        trust: TrustLevel = TrustLevel.TRUSTED,
        provenance: ProvenanceClass = ProvenanceClass.TRUSTED,
        when: datetime = NOW,
    ) -> str:
        if await self.episodes.episode("e1") is None:
            await self.episodes.open_episode(Episode(id="e1", session_id="s1", started_at=when))
        await self.episodes.append(
            Utterance(
                id=utterance_id,
                episode_id="e1",
                turn_index=int(utterance_id[1:]),
                speaker="user",
                text=text,
                provenance_class=provenance,
                trust_level=trust,
                occurred_at=when,
            )
        )
        return utterance_id

    def rows(self) -> int:
        with self.db.transaction() as conn:
            return int(one(conn.execute("SELECT COUNT(*) FROM memories"))[0])

    def close(self) -> None:
        self.db.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    fixture = Rig()
    try:
        yield fixture
    finally:
        fixture.close()


def belief(
    content: str = "ユーザーは Factorio が好き",
    *,
    mode: AssertionMode = AssertionMode.USER_STATED,
    evidence: tuple[str, ...] = ("u1",),
    kind: MemoryType = MemoryType.SEMANTIC,
    subject: str = "user.hobby",
    trust: TrustLevel = TrustLevel.TRUSTED,
    provenance: ProvenanceClass = ProvenanceClass.TRUSTED,
    salience: float = 0.6,
    confidence: float = 0.8,
) -> MemoryCandidate:
    return MemoryCandidate(
        type=kind,
        subject=subject,
        content=content,
        assertion_mode=mode,
        evidence_ref=evidence,
        confidence=confidence,
        provenance_class=provenance,
        trust_level=trust,
        base_salience=salience,
    )


# ── What is refused ──────────────────────────────────────────


async def test_a_candidate_cannot_call_itself_confirmed(rig: Rig) -> None:
    """★ **Invariant 7.** `confirm()` is the only escalation path, and a candidate allowed
    to claim `user_confirmed` would quietly be a second one — an extractor could reason
    "the user said it, so it is confirmed" and write itself a promotion.
    """
    await rig.say("u1")

    with pytest.raises(MemoryRejected, match="confirm"):
        await rig.store.write(belief(mode=AssertionMode.USER_CONFIRMED), now=NOW)

    assert rig.rows() == 0


async def test_evidence_that_does_not_exist_is_refused(rig: Rig) -> None:
    """★ "assertion_mode の検証" (§4). A belief citing an utterance nobody said is not
    weakly grounded — **it is invented**, and writing it would put a fabricated citation
    in front of a future Reflection Job.
    """
    with pytest.raises(MemoryRejected, match="u404"):
        await rig.store.write(belief(evidence=("u404",)), now=NOW)


async def test_what_the_user_said_must_cite_what_they_said(rig: Rig) -> None:
    """`user_stated` and `inferred` both claim the conversation as their source."""
    with pytest.raises(MemoryRejected, match="utterance"):
        await rig.store.write(belief(evidence=()), now=NOW)


async def test_lumis_own_guess_needs_no_evidence(rig: Rig) -> None:
    """`self_generated` did not come from an utterance, and pretending otherwise would
    force the extractor to invent a citation.
    """
    record = await rig.store.write(belief(mode=AssertionMode.SELF_GENERATED, evidence=()), now=NOW)

    assert record.evidence_ref == ()


async def test_an_empty_memory_is_refused(rig: Rig) -> None:
    await rig.say("u1")

    with pytest.raises(MemoryRejected):
        await rig.store.write(belief(content="   "), now=NOW)


async def test_an_out_of_range_confidence_is_rounded(rig: Rig) -> None:
    """Same rule as salience: **a formatting slip should not cost a memory.**"""
    await rig.say("u1")

    record = await rig.store.write(belief(confidence=1.7), now=NOW)

    assert record.confidence == 1.0


# ── Trust travels with the belief ────────────────────────────


async def test_a_belief_made_from_tainted_speech_is_derived(rig: Rig) -> None:
    """★ Test 8. The extractor may declare itself trusted; **what it was made of decides.**

    Summarizing a web page does not clean it (Invariant 7), and the same is true of
    turning it into a memory.
    """
    await rig.say(
        "u1",
        text="このページには…",
        trust=TrustLevel.TAINTED,
        provenance=ProvenanceClass.UNTRUSTED,
    )

    record = await rig.store.write(belief(trust=TrustLevel.TRUSTED), now=NOW)

    assert record.trust_level is TrustLevel.TAINTED
    assert record.provenance_class is ProvenanceClass.DERIVED


async def test_a_belief_from_trusted_speech_stays_trusted(rig: Rig) -> None:
    """**Not everything is tainted.** A memory from ordinary conversation is a function of
    what the user said and Lumi's own state; treating it as tainted would leave the
    escalation rule with nothing to discriminate.
    """
    await rig.say("u1")

    record = await rig.store.write(belief(), now=NOW)

    assert record.trust_level is TrustLevel.TRUSTED
    assert record.provenance_class is ProvenanceClass.TRUSTED


async def test_raw_external_content_keeps_saying_it_is_external(rig: Rig) -> None:
    record = await rig.store.write(
        belief(
            mode=AssertionMode.EXTERNAL,
            evidence=(),
            provenance=ProvenanceClass.UNTRUSTED,
            trust=TrustLevel.TAINTED,
        ),
        now=NOW,
    )

    assert record.provenance_class is ProvenanceClass.UNTRUSTED
    assert record.trust_level is TrustLevel.TAINTED


# ── Confirmation is the one escalation ───────────────────────


async def test_confirm_is_the_only_path_to_trusted(rig: Rig) -> None:
    """★ Test 9 / Invariant 7. The user looked at the record and said it is right."""
    await rig.say("u1", trust=TrustLevel.TAINTED, provenance=ProvenanceClass.UNTRUSTED)
    record = await rig.store.write(belief(), now=NOW)
    assert record.trust_level is TrustLevel.TAINTED

    confirmed = await rig.store.confirm(record.id, now=NOW)

    assert confirmed.trust_level is TrustLevel.TRUSTED
    assert confirmed.assertion_mode is AssertionMode.USER_CONFIRMED
    assert confirmed.provenance_class is ProvenanceClass.TRUSTED


async def test_confirming_brings_a_faded_memory_back(rig: Rig) -> None:
    """Something the user just vouched for is not something Lumi has stopped recalling."""
    await rig.say("u1")
    record = await rig.store.write(belief(salience=0.1), now=NOW)
    await rig.store.archive(record.id, now=NOW)

    confirmed = await rig.store.confirm(record.id, now=NOW)

    assert confirmed.archived_at is None
    assert confirmed.is_live


async def test_confirming_something_that_is_not_there_fails(rig: Rig) -> None:
    with pytest.raises(MemoryRejected):
        await rig.store.confirm("nope", now=NOW)


# ── Forgetting ───────────────────────────────────────────────


async def test_archiving_hides_a_memory_without_deleting_it(rig: Rig) -> None:
    """★ Tests 2 and 3. **"思い出せなくなる" であって "無かったことになる" ではない。**"""
    await rig.say("u1")
    record = await rig.store.write(belief(), now=NOW)

    await rig.store.archive(record.id, now=NOW)

    assert await rig.store.live("user.hobby") == []
    kept = await rig.store.get(record.id)
    assert kept is not None
    assert kept.archived_at == NOW
    assert rig.rows() == 1


async def test_what_faded_below_the_floor_is_archived(rig: Rig) -> None:
    await rig.say("u1")
    faded = await rig.store.write(belief(salience=FLOOR / 2), now=NOW)
    kept = await rig.store.write(
        belief(content="ユーザーは猫を飼っている", subject="user.pet"), now=NOW
    )

    archived = await rig.store.archive_faded(now=NOW)

    assert list(archived) == [faded.id]
    still_there = await rig.store.get(kept.id)
    assert still_there is not None and still_there.is_live


async def test_a_recalled_memory_is_held_a_little_longer(rig: Rig) -> None:
    """`touch()` is what retrieval calls. It records **that** something was recalled, never
    what it was recalled for.
    """
    await rig.say("u1")
    record = await rig.store.write(belief(), now=NOW)

    later = NOW + timedelta(days=10)
    await rig.store.touch((record.id,), now=later)

    refreshed = await rig.store.get(record.id)
    assert refreshed is not None
    assert refreshed.access_count == 1
    assert refreshed.last_accessed == later


# ── Being contradicted ───────────────────────────────────────


async def test_superseding_leaves_the_old_belief_readable(rig: Rig) -> None:
    """★ Test 4. **The old row is not edited.** This is what lets Lumi say
    「前は Factorio 好きって言ってたけど、最近はどう?」 rather than only ever knowing the
    current value.
    """
    await rig.say("u1")
    await rig.say("u2", text="最近は Rimworld ばっかり")
    old = await rig.store.write(belief(), now=NOW)

    later = NOW + timedelta(days=150)
    outcome = await rig.store.supersede(
        old.id, belief(content="最近は Rimworld をやっている", evidence=("u2",)), now=later
    )

    superseded = await rig.store.get(old.id)
    assert superseded is not None
    assert superseded.content == "ユーザーは Factorio が好き"
    assert superseded.superseded_by == outcome.record.id
    assert outcome.record.valid_from == later
    live = [record.id for record in await rig.store.live("user.hobby")]
    assert outcome.record.id in live
    assert old.id not in live


async def test_the_disagreement_itself_is_remembered(rig: Rig) -> None:
    """★ Test 6. Without this, superseding is indistinguishable from a silent overwrite:
    the new belief is there and **nothing remembers that it changed.**
    """
    await rig.say("u1")
    await rig.say("u2", text="最近は Rimworld ばっかり")
    old = await rig.store.write(belief(), now=NOW)

    outcome = await rig.store.supersede(
        old.id, belief(content="最近は Rimworld をやっている", evidence=("u2",)), now=NOW
    )

    assert outcome.note is not None
    assert outcome.note.type is MemoryType.EPISODIC
    # **Not `user_stated`** — the user never said "I changed my mind"; Lumi noticed.
    assert outcome.note.assertion_mode is AssertionMode.INFERRED
    assert "Factorio" in outcome.note.content and "Rimworld" in outcome.note.content


async def test_a_note_survives_the_conversation_it_came_from(rig: Rig) -> None:
    """★ The older belief may cite utterances that expired months ago. **A note is not
    refused because the episode behind it is past its retention** — that would make
    contradictions unrecordable precisely for the long-held beliefs that matter most.
    """
    await rig.say("u1")
    old = await rig.store.write(belief(), now=NOW)
    with rig.db.transaction() as conn:
        conn.execute("DELETE FROM utterances")  # the retention job, 90 days later
        conn.execute("DELETE FROM episodes")

    outcome = await rig.store.supersede(
        old.id,
        belief(
            content="最近は Rimworld をやっている", mode=AssertionMode.SELF_GENERATED, evidence=()
        ),
        now=NOW + timedelta(days=120),
    )

    assert outcome.note is not None


async def test_a_belief_outlives_the_episode_it_was_made_from(rig: Rig) -> None:
    """**Deleting the log does not delete what was learned from it** (privacy.md §4).

    The evidence reference is deliberately not a foreign key: with one, retention would
    either be unable to delete the episode or would take the belief with it.
    """
    await rig.say("u1")
    record = await rig.store.write(belief(), now=NOW)

    with rig.db.transaction() as conn:
        conn.execute("DELETE FROM utterances")
        conn.execute("DELETE FROM episodes")

    kept = await rig.store.get(record.id)
    assert kept is not None
    assert kept.evidence_ref == ("u1",)


# ── Reconciling a candidate against what is already believed ──


async def test_the_first_belief_about_a_subject_is_just_written(rig: Rig) -> None:
    await rig.say("u1")

    outcome = await rig.store.reconcile(belief(), now=NOW)

    assert outcome.resolution is Resolution.NEW
    assert outcome.superseded_id is None


async def test_saying_the_same_thing_again_reinforces_rather_than_duplicates(rig: Rig) -> None:
    """Two identical rows would be two beliefs, and retrieval would spend its budget
    saying the same thing twice.
    """
    await rig.say("u1")
    first = await rig.store.reconcile(belief(), now=NOW)

    again = await rig.store.reconcile(belief(), now=NOW + timedelta(days=1))

    assert again.resolution is Resolution.DUPLICATE
    assert again.record.id == first.record.id
    assert again.record.access_count == 1
    assert rig.rows() == 1


async def test_a_better_grounded_claim_takes_over(rig: Rig) -> None:
    await rig.say("u1")
    await rig.say("u2", text="いや、いまは Rimworld")
    await rig.store.reconcile(belief(mode=AssertionMode.INFERRED), now=NOW)

    outcome = await rig.store.reconcile(
        belief(content="最近は Rimworld をやっている", evidence=("u2",)), now=NOW
    )

    assert outcome.resolution is Resolution.SUPERSEDE
    assert outcome.superseded_id is not None


async def test_a_weaker_claim_is_kept_but_does_not_take_over(rig: Rig) -> None:
    """★ **Kept, with its confidence cut.** Discarding it would lose a guess that might
    turn out right; promoting it would let a guess outrank what the user said.
    """
    await rig.say("u1")
    stated = await rig.store.reconcile(belief(), now=NOW)

    outcome = await rig.store.reconcile(
        belief(
            content="ユーザーは Rimworld が好き",
            mode=AssertionMode.SELF_GENERATED,
            evidence=(),
            confidence=0.8,
        ),
        now=NOW,
    )

    assert outcome.resolution is Resolution.KEEP_WEAK
    assert outcome.record.confidence == pytest.approx(0.4)
    live = {record.id for record in await rig.store.live("user.hobby")}
    assert stated.record.id in live and outcome.record.id in live


async def test_two_things_that_happened_are_not_a_contradiction(rig: Rig) -> None:
    """★ Only semantic beliefs conflict. Two episodic records of different moments are not
    a disagreement to resolve — **they are two things that happened**, and superseding one
    with the other would erase a day from Lumi's account of the week.
    """
    await rig.say("u1")
    monday = await rig.store.reconcile(
        belief(content="月曜に Factorio の話をした", kind=MemoryType.EPISODIC), now=NOW
    )

    tuesday = await rig.store.reconcile(
        belief(content="火曜に Rimworld の話をした", kind=MemoryType.EPISODIC), now=NOW
    )

    assert tuesday.resolution is Resolution.NEW
    live = {record.id for record in await rig.store.live("user.hobby")}
    assert {monday.record.id, tuesday.record.id} <= live


async def test_an_archived_belief_does_not_block_a_new_one(rig: Rig) -> None:
    """A memory Lumi can no longer recall cannot be the thing it argues against."""
    await rig.say("u1")
    old = await rig.store.reconcile(belief(), now=NOW)
    await rig.store.archive(old.record.id, now=NOW)

    outcome = await rig.store.reconcile(belief(content="最近は Rimworld をやっている"), now=NOW)

    assert outcome.resolution is Resolution.NEW


# ── The store does not delete ────────────────────────────────


def test_only_one_file_in_core_deletes_user_data() -> None:
    """★ **privacy.md §5**, as a check rather than an intention.

    The audit log has to be "unreachable from every Tool path, reachable from retention
    and from erase-everything, and nowhere else" — and a boundary spread across modules
    is one nobody can verify. A `purge()` on the store would have been the second place,
    which is why the memory UI's delete lives in the retention service instead.
    """
    offenders = sorted(
        path.relative_to(CORE).as_posix()
        for path in CORE.joinpath("lumi").rglob("*.py")
        if re.search(r"DELETE\s+FROM", path.read_text(encoding="utf-8"))
    )

    assert offenders == ["lumi/storage/retention.py"]
    assert not hasattr(MemoryStore, "purge")


# ── A database written by the previous release ───────────────


async def test_a_2c_memory_database_gains_memories_without_losing_a_word(tmp_path: Path) -> None:
    """★ Test 10 / 12. **The conversation a user already has on disk must survive.**

    2c shipped `memory.db` with episodes and utterances in it. This is that file, opened
    by the release that adds memory records: the log is still there, the version has
    moved, and the new tables exist.
    """
    key = "ab" * 32
    path = tmp_path / "memory.db"
    as_shipped_in_2c = Schema(component="storage.memory", migrations=MEMORY_SCHEMA.migrations[:1])

    old = Database.open(path, as_shipped_in_2c, key=key)
    try:
        await EpisodeStore(old).open_episode(Episode(id="e1", session_id="s1", started_at=NOW))
        await EpisodeStore(old).append(
            Utterance(
                id="u1",
                episode_id="e1",
                turn_index=0,
                speaker="user",
                text="おはよう",
                provenance_class=ProvenanceClass.TRUSTED,
                trust_level=TrustLevel.TRUSTED,
                occurred_at=NOW,
            )
        )
    finally:
        old.close()

    upgraded = Database.open(path, MEMORY_SCHEMA, key=key)
    try:
        lines = await EpisodeStore(upgraded).utterances("e1")
        assert [line.text for line in lines] == ["おはよう"]

        record = await MemoryStore(upgraded).write(belief(), now=NOW)
        assert record.evidence_ref == ("u1",)
        with upgraded.transaction() as conn:
            assert one(conn.execute("SELECT version FROM _schema_version"))[0] == 2
    finally:
        upgraded.close()
