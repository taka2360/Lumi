"""Memory records on disk. **The only writer of what Lumi believes.**

Design → docs/architecture/memory.md §3 / §5 / §6 · Interface → docs/interfaces/memory.md

## It does not take the candidate's word for anything

A candidate comes out of an LLM. This class checks it before any of it becomes something
Lumi believes:

| Checked | Why |
|---|---|
| `user_confirmed` is refused | `confirm()` is the **only** escalation path (Invariant 7);
  a candidate allowed to claim it would be a second one |
| Evidence must exist | "assertion_mode の検証" (§4). A belief citing an utterance
  nobody said is not weakly grounded, it is invented |
| Trust is **joined** with the evidence's | Trust comes from where the sentence came
  from, and **no automatic step may raise it** (Invariant 7) |
| Empty subject/content is refused | A memory with nothing in it is a row, not a memory |

The join can only move toward taint, so it is propagation and not escalation. The one
place in this file that writes `TrustLevel.TRUSTED` is `confirm()`.

## Nothing here deletes

`archive()` is an `UPDATE`. Physical deletion lives in `lumi.storage.retention` with every
other `DELETE` against user data, because the boundary in docs/contracts/privacy.md §5 has
to be checkable by reading one file.

## One call is one transaction, and `_..._in` says so

Every public method hands one function to `Database.in_transaction`, which opens the
transaction, runs it and commits **inside a single thread hop.** The `_..._in` suffix marks
the ones that run there: they take the `conn` and never open their own, so `reconcile` can
read, touch and insert without any of it racing another pass.

**The shape this rules out is `with transaction(): await ...`** — the connection is shared
and guarded by a `threading.Lock`, so holding it across an await blocks every other caller
for as long as the await takes, and the transaction outlives the thread that opened it.
Written by hand that shape is one line away from the right one; there is no way to write it
through `in_transaction` at all.

## Superseding writes two rows and edits one column

The new belief is inserted, the old row's `superseded_by` is pointed at it, and **an
episodic note about the disagreement is written in the same transaction** — not by the
caller, because a rule that depends on every caller remembering it is one that eventually
produces a silent overwrite (docs/architecture/memory.md §6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

import apsw

from lumi import logging as lumi_logging
from lumi.memory import decay
from lumi.memory.contradiction import (
    WEAK_CONFIDENCE_FACTOR,
    Resolution,
    contradiction_note,
    normalize,
    resolve,
)
from lumi.memory.records import AssertionMode, MemoryCandidate, MemoryRecord, MemoryType
from lumi.memory.rows import COLUMNS, hydrate, read
from lumi.provenance import (
    ProvenanceClass,
    TrustLevel,
    join,
    join_all,
    provenance_from,
    taint,
)
from lumi.storage.sqlite import Database

log = lumi_logging.get_logger(__name__)

#: Assertion modes that claim the conversation as their source, and therefore have to name
#: at least one utterance. `self_generated` and `external` did not come from one.
GROUNDED_IN_CONVERSATION: Final = (AssertionMode.USER_STATED, AssertionMode.INFERRED)


class MemoryRejected(ValueError):
    """The candidate is not something that may be believed. **Refused, not repaired.**"""


@dataclass(frozen=True, slots=True)
class Reconciled:
    """What happened to a candidate that met an existing belief about the same subject."""

    record: MemoryRecord
    resolution: Resolution
    #: The belief that now has a successor, if any.
    superseded_id: str | None = None
    #: The episodic record of the disagreement, if one was written.
    note: MemoryRecord | None = None


class MemoryStore:
    """Reads and writes memory records. **Blocking I/O runs off the event loop.**"""

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    # ── writing ────────────────────────────────────────────────────────────

    async def write(
        self, candidate: MemoryCandidate, *, now: datetime | None = None
    ) -> MemoryRecord:
        """Believe this. Raises `MemoryRejected` if the candidate does not check out."""
        moment = now or datetime.now(UTC)
        return await self._db.in_transaction(lambda conn: self._insert(conn, candidate, moment))

    async def supersede(
        self, old_id: str, candidate: MemoryCandidate, *, now: datetime | None = None
    ) -> Reconciled:
        """Replace a belief **without overwriting it.** The old row stays as it was."""
        moment = now or datetime.now(UTC)
        return await self._db.in_transaction(
            lambda conn: self._supersede_by_id(conn, old_id, candidate, moment)
        )

    def _supersede_by_id(
        self, conn: apsw.Connection, old_id: str, candidate: MemoryCandidate, now: datetime
    ) -> Reconciled:
        existing = read(conn, old_id)
        if existing is None:
            raise MemoryRejected(f"No such memory to supersede: {old_id}")
        return self._supersede_in(conn, existing, candidate, now)

    async def reconcile(
        self, candidate: MemoryCandidate, *, now: datetime | None = None
    ) -> Reconciled:
        """Write the candidate, taking existing beliefs about the same subject into account.

        **The decision is `contradiction.resolve`**, a pure function — this only carries it
        out, in **one transaction**, so that two passes cannot each read "no conflict" and
        then both write a live belief about the same subject.
        """
        moment = now or datetime.now(UTC)
        return await self._db.in_transaction(
            lambda conn: self._reconcile_in(conn, candidate, moment)
        )

    def _reconcile_in(
        self, conn: apsw.Connection, candidate: MemoryCandidate, now: datetime
    ) -> Reconciled:
        content = normalize(candidate.content)
        for record in self._live_rows(conn, candidate.subject, candidate.type):
            if normalize(record.content) == content:
                # **Saying it again is not a second belief.** It is a reason to hold
                # the one already there a little more firmly.
                self._touch_in(conn, (record.id,), now)
                refreshed = read(conn, record.id)
                assert refreshed is not None  # touched in this transaction
                return Reconciled(record=refreshed, resolution=Resolution.DUPLICATE)

        # **Only a belief can contradict a belief.** An episodic record about the same
        # subject is a thing that happened, and letting one into this comparison would
        # let 「月曜に Factorio の話をした」 supersede 「ユーザーは Factorio が好き」 —
        # a moment quietly replacing what Lumi knows.
        conflicts = (
            [
                record
                for record in self._live_rows(conn, candidate.subject, MemoryType.SEMANTIC)
                if normalize(record.content) != content
            ]
            if candidate.type is MemoryType.SEMANTIC
            else []
        )
        if not conflicts:
            return Reconciled(record=self._insert(conn, candidate, now), resolution=Resolution.NEW)

        existing = conflicts[0]
        if resolve(existing, candidate) is Resolution.SUPERSEDE:
            return self._supersede_in(conn, existing, candidate, now)
        # **Kept, not dropped.** A weaker claim that turns out to be right should still
        # be findable; it just does not get to outrank what the user said.
        weakened = replace(candidate, confidence=candidate.confidence * WEAK_CONFIDENCE_FACTOR)
        return Reconciled(record=self._insert(conn, weakened, now), resolution=Resolution.KEEP_WEAK)

    def _supersede_in(
        self,
        conn: apsw.Connection,
        existing: MemoryRecord,
        candidate: MemoryCandidate,
        now: datetime,
    ) -> Reconciled:
        record = self._insert(conn, candidate, now)
        conn.execute("UPDATE memories SET superseded_by = ? WHERE id = ?", (record.id, existing.id))
        note = self._insert(
            conn,
            MemoryCandidate(
                type=MemoryType.EPISODIC,
                subject=existing.subject,
                content=contradiction_note(existing, candidate.content),
                # **Not `user_stated`.** The user did not say "I changed my mind"; Lumi
                # noticed it by comparing two of its own records.
                assertion_mode=AssertionMode.INFERRED,
                # The note is no more trustworthy than the beliefs it is about.
                provenance_class=existing.provenance_class,
                trust_level=join(existing.trust_level, candidate.trust_level),
                evidence_ref=existing.evidence_ref + candidate.evidence_ref,
                confidence=1.0,
                base_salience=max(existing.base_salience, candidate.base_salience),
                source_episode_ids=existing.source_episode_ids + candidate.source_episode_ids,
            ),
            now,
            # The older belief may cite utterances that have since expired. **A note is
            # not refused because the conversation it descends from is past its retention.**
            require_evidence=False,
        )
        log.info(
            "memory.superseded", subject=existing.subject, old_id=existing.id, new_id=record.id
        )
        return Reconciled(
            record=record,
            resolution=Resolution.SUPERSEDE,
            superseded_id=existing.id,
            note=note,
        )

    # ── reading ────────────────────────────────────────────────────────────

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return await self._db.in_transaction(lambda conn: read(conn, memory_id))

    async def live(self, subject: str) -> Sequence[MemoryRecord]:
        """Beliefs about `subject` that are neither superseded nor archived."""
        return await self._db.in_transaction(lambda conn: self._live_rows(conn, subject, None))

    async def find_conflicts(self, subject: str, content: str) -> Sequence[MemoryRecord]:
        """Live semantic beliefs about the same subject that say something else.

        **Only semantic memories conflict.** Two episodic records of different moments are
        not a disagreement; they are two things that happened.

        Whether two *differently worded* sentences mean the same thing needs embeddings
        (2e). Until then this compares text: it **cannot** tell, as opposed to having
        checked and found nothing.
        """
        records = await self._db.in_transaction(
            lambda conn: self._live_rows(conn, subject, MemoryType.SEMANTIC)
        )
        target = normalize(content)
        return [record for record in records if normalize(record.content) != target]

    async def recent(self, limit: int) -> Sequence[MemoryRecord]:
        """The newest live beliefs, regardless of what was asked.

        Retrieval unions this with the two indexes: **something said five minutes ago is
        relevant in a way no similarity score captures**, and a brand-new memory that has
        not been embedded yet would otherwise be invisible until the next index pass.
        """
        if limit <= 0:
            return []
        return await self._db.in_transaction(lambda conn: self._recent_in(conn, limit))

    def _recent_in(self, conn: apsw.Connection, limit: int) -> list[MemoryRecord]:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM memories"
            " WHERE superseded_by IS NULL AND archived_at IS NULL"
            " ORDER BY valid_from DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [hydrate(conn, row) for row in rows]

    async def get_many(self, memory_ids: Sequence[str]) -> dict[str, MemoryRecord]:
        """Several records at once, **by id, in no particular order.**"""
        if not memory_ids:
            return {}
        wanted = tuple(dict.fromkeys(memory_ids))
        records = await self._db.in_transaction(lambda conn: self._by_ids_in(conn, wanted))
        return {record.id: record for record in records}

    def _by_ids_in(self, conn: apsw.Connection, memory_ids: tuple[str, ...]) -> list[MemoryRecord]:
        placeholders = ", ".join("?" * len(memory_ids))
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM memories WHERE id IN ({placeholders})", memory_ids
        ).fetchall()
        return [hydrate(conn, row) for row in rows]

    async def needing_embedding(self, model_id: str, *, limit: int) -> Sequence[MemoryRecord]:
        """Records whose vector is missing or was made by a different model.

        **This is the re-embedding trigger** (docs/interfaces/memory.md): a changed
        `embedding_model_id` is how a model swap is detected, rather than by search quality
        slowly getting worse for reasons nobody can point at.

        Superseded beliefs are skipped — nothing searches them, so embedding them would be
        work done to make a row nobody reads slightly more findable.
        """
        if limit <= 0:
            return []
        return await self._db.in_transaction(lambda conn: self._needing_in(conn, model_id, limit))

    def _needing_in(self, conn: apsw.Connection, model_id: str, limit: int) -> list[MemoryRecord]:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM memories"
            " WHERE superseded_by IS NULL AND embedding_model_id != ?"
            " ORDER BY created_at LIMIT ?",
            (model_id, limit),
        ).fetchall()
        return [hydrate(conn, row) for row in rows]

    async def mark_embedded(self, memory_ids: Sequence[str], model_id: str) -> None:
        """Record which model produced these vectors. **Written after the index, never
        before** — the other order claims an embedding that may not exist.
        """
        if not memory_ids:
            return
        wanted = tuple(memory_ids)
        await self._db.in_transaction(lambda conn: self._mark_in(conn, wanted, model_id))

    def _mark_in(self, conn: apsw.Connection, memory_ids: tuple[str, ...], model_id: str) -> None:
        for memory_id in memory_ids:
            conn.execute(
                "UPDATE memories SET embedding_model_id = ? WHERE id = ?",
                (model_id, memory_id),
            )

    def _live_rows(
        self, conn: apsw.Connection, subject: str, kind: MemoryType | None
    ) -> list[MemoryRecord]:
        sql = (
            f"SELECT {COLUMNS} FROM memories"
            " WHERE subject = ? AND superseded_by IS NULL AND archived_at IS NULL"
        )
        parameters: list[Any] = [subject]
        if kind is not None:
            sql += " AND type = ?"
            parameters.append(kind.value)
        sql += " ORDER BY valid_from DESC, created_at DESC"
        rows = conn.execute(sql, tuple(parameters)).fetchall()
        return [hydrate(conn, row) for row in rows]

    # ── decay, recall and confirmation ─────────────────────────────────────

    async def touch(self, memory_ids: Sequence[str], *, now: datetime | None = None) -> None:
        """Record that these were recalled. **Feeds `access_boost`, not the content.**"""
        if not memory_ids:
            return
        moment = now or datetime.now(UTC)
        wanted = tuple(memory_ids)
        await self._db.in_transaction(lambda conn: self._touch_in(conn, wanted, moment))

    def _touch_in(self, conn: apsw.Connection, memory_ids: tuple[str, ...], now: datetime) -> None:
        for memory_id in memory_ids:
            conn.execute(
                "UPDATE memories SET last_accessed = ?, access_count = access_count + 1"
                " WHERE id = ?",
                (now.isoformat(), memory_id),
            )

    async def archive(self, memory_id: str, *, now: datetime | None = None) -> None:
        """Stop recalling it. **Nothing is deleted** (docs/architecture/memory.md §5)."""
        moment = now or datetime.now(UTC)
        await self._db.in_transaction(lambda conn: self._archive_in(conn, (memory_id,), moment))

    def _archive_in(
        self, conn: apsw.Connection, memory_ids: tuple[str, ...], now: datetime
    ) -> None:
        for memory_id in memory_ids:
            conn.execute(
                "UPDATE memories SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
                (now.isoformat(), memory_id),
            )

    async def archive_faded(self, *, now: datetime | None = None) -> Sequence[str]:
        """Archive everything that has decayed below the floor. **Returns what faded.**

        The decision is `decay.is_faded` in Python rather than arithmetic in SQL: the curve
        is the thing being tested, and a second copy of it written in SQL would be a second
        thing to keep in agreement with the design doc.
        """
        moment = now or datetime.now(UTC)
        faded = await self._db.in_transaction(lambda conn: self._fade_in(conn, moment))
        if faded:
            log.info("memory.archived", count=len(faded))
        return faded

    def _fade_in(self, conn: apsw.Connection, now: datetime) -> Sequence[str]:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM memories WHERE archived_at IS NULL AND superseded_by IS NULL"
        ).fetchall()
        faded = [
            record.id
            for record in (hydrate(conn, row, evidence=False) for row in rows)
            if decay.is_faded(record, now)
        ]
        for memory_id in faded:
            conn.execute(
                "UPDATE memories SET archived_at = ? WHERE id = ?", (now.isoformat(), memory_id)
            )
        return faded

    async def confirm(self, memory_id: str, *, now: datetime | None = None) -> MemoryRecord:
        """**The only path from `tainted` to `trusted`** (Invariant 7).

        Called from the memory UI's handler and nowhere else: the user looked at this
        record and said it is right. Being confirmed also brings it back from archived —
        something the user just vouched for is not something Lumi has stopped recalling.
        """
        moment = now or datetime.now(UTC)
        confirmed = await self._db.in_transaction(
            lambda conn: self._confirm_one(conn, memory_id, moment)
        )
        log.info("memory.confirmed", memory_id=memory_id, subject=confirmed.subject)
        return confirmed

    def _confirm_one(self, conn: apsw.Connection, memory_id: str, now: datetime) -> MemoryRecord:
        existing = read(conn, memory_id)
        if existing is None:
            raise MemoryRejected(f"No such memory to confirm: {memory_id}")
        self._confirm_in(conn, memory_id, now)
        confirmed = read(conn, memory_id)
        assert confirmed is not None  # read back inside the same transaction
        return confirmed

    def _confirm_in(self, conn: apsw.Connection, memory_id: str, now: datetime) -> None:
        """The escalation itself. **Kept in one place** so `rewrite` cannot grow a
        second copy of the only `TRUSTED` assignment outside direct user input.
        """
        conn.execute(
            "UPDATE memories SET assertion_mode = ?, trust_level = ?, provenance_class = ?,"
            " archived_at = NULL, last_accessed = ? WHERE id = ?",
            (
                AssertionMode.USER_CONFIRMED.value,
                # **One of the two places in Lumi that may write this** (the other is
                # the handler for direct user input). docs/contracts/provenance.md.
                TrustLevel.TRUSTED.value,
                ProvenanceClass.TRUSTED.value,
                now.isoformat(),
                memory_id,
            ),
        )

    async def rewrite(
        self,
        memory_id: str,
        *,
        content: str,
        subject: str | None = None,
        now: datetime | None = None,
    ) -> MemoryRecord:
        """The user corrected a memory from the memory window (docs/architecture/ui.md §5b).

        **Supersedes rather than overwrites**, like every other change of belief: what
        Lumi thought before stays readable, and the correction is a record with its own
        timestamp. The new record is `user_confirmed` and `TRUSTED` — the user wrote this
        sentence themselves, and there is no grounds stronger than that (Invariant 7).

        **No contradiction note is written.** The note exists so Lumi can say "you told me
        something different before"; a person fixing Lumi's own transcription of them did
        not tell it something different, and saying so would be an accusation built out of
        a typo.
        """
        moment = now or datetime.now(UTC)
        old_id, rewritten = await self._db.in_transaction(
            lambda conn: self._rewrite_in(conn, memory_id, content, subject, moment)
        )
        log.info("memory.rewritten", old_id=old_id, new_id=rewritten.id, subject=rewritten.subject)
        return rewritten

    def _rewrite_in(
        self,
        conn: apsw.Connection,
        memory_id: str,
        content: str,
        subject: str | None,
        now: datetime,
    ) -> tuple[str, MemoryRecord]:
        """The correction itself. **Returns the superseded id alongside the new record** —
        the log line names both, and it is written after the commit rather than before it.
        """
        existing = read(conn, memory_id)
        if existing is None:
            raise MemoryRejected(f"No such memory to rewrite: {memory_id}")
        if existing.superseded_by is not None:
            # ★ **A superseded belief has already been replaced.** Correcting it would
            # give one row two successors, and `_live_rows` would then return two live
            # beliefs about the same subject — a contradiction Lumi made by itself.
            # The window shows history read-only; the thing to correct is the current
            # belief (docs/architecture/memory.md §8).
            raise MemoryRejected(f"Already superseded, correct its successor: {memory_id}")
        record = self._insert(
            conn,
            MemoryCandidate(
                type=existing.type,
                subject=subject if subject is not None else existing.subject,
                content=content,
                # **Written as `user_stated`, then escalated.** `_insert` refuses
                # `user_confirmed` on purpose (Invariant 7), and routing around that
                # check here would put a second escalation path in the codebase.
                assertion_mode=AssertionMode.USER_STATED,
                provenance_class=existing.provenance_class,
                trust_level=existing.trust_level,
                evidence_ref=existing.evidence_ref,
                confidence=existing.confidence,
                base_salience=existing.base_salience,
                valid_from=now,
                source_episode_ids=existing.source_episode_ids,
            ),
            now,
            # The memory being corrected may cite utterances that have since expired.
            # **A correction is not refused because the conversation it came from is
            # past its retention** — that is exactly when a correction is needed.
            require_evidence=False,
        )
        conn.execute("UPDATE memories SET superseded_by = ? WHERE id = ?", (record.id, existing.id))
        self._confirm_in(conn, record.id, now)
        rewritten = read(conn, record.id)
        assert rewritten is not None  # read back inside the same transaction
        return existing.id, rewritten

    # ── internals ──────────────────────────────────────────────────────────

    def _insert(
        self,
        conn: apsw.Connection,
        candidate: MemoryCandidate,
        now: datetime,
        *,
        require_evidence: bool = True,
    ) -> MemoryRecord:
        """Validate, resolve trust from the evidence, and write the row."""
        subject = candidate.subject.strip()
        content = candidate.content.strip()
        if not subject or not content:
            raise MemoryRejected("A memory needs both a subject and content")
        if candidate.assertion_mode is AssertionMode.USER_CONFIRMED:
            raise MemoryRejected(
                "user_confirmed is set by MemoryStore.confirm() only (Invariant 7)"
            )
        if (
            require_evidence
            and candidate.assertion_mode in GROUNDED_IN_CONVERSATION
            and not candidate.evidence_ref
        ):
            raise MemoryRejected(
                f"{candidate.assertion_mode.value} must cite at least one utterance"
            )

        trust, provenance = self._resolve_trust(conn, candidate, require_evidence=require_evidence)
        record = MemoryRecord(
            id=uuid4().hex,
            type=candidate.type,
            subject=subject,
            content=content,
            assertion_mode=candidate.assertion_mode,
            evidence_ref=candidate.evidence_ref,
            confidence=decay.clamp01(candidate.confidence, name="confidence"),
            provenance_class=provenance,
            trust_level=trust,
            base_salience=decay.clamp01(candidate.base_salience, name="base_salience"),
            created_at=now,
            last_accessed=now,
            access_count=0,
            archived_at=None,
            valid_from=candidate.valid_from or now,
            superseded_by=None,
            source_episode_ids=candidate.source_episode_ids,
        )
        conn.execute(
            f"INSERT INTO memories ({COLUMNS}) VALUES ({', '.join('?' * 16)})",
            (
                record.id,
                record.type.value,
                record.subject,
                record.content,
                record.assertion_mode.value,
                record.confidence,
                record.provenance_class.value,
                record.trust_level.value,
                record.base_salience,
                record.created_at.isoformat(),
                record.last_accessed.isoformat(),
                record.access_count,
                None,
                record.valid_from.isoformat(),
                None,
                record.embedding_model_id,
            ),
        )
        for utterance_id in dict.fromkeys(record.evidence_ref):
            conn.execute(
                "INSERT INTO memory_evidence (memory_id, utterance_id) VALUES (?, ?)",
                (record.id, utterance_id),
            )
        for episode_id in dict.fromkeys(record.source_episode_ids):
            conn.execute(
                "INSERT INTO memory_sources (memory_id, episode_id) VALUES (?, ?)",
                (record.id, episode_id),
            )
        return record

    def _resolve_trust(
        self, conn: apsw.Connection, candidate: MemoryCandidate, *, require_evidence: bool
    ) -> tuple[TrustLevel, ProvenanceClass]:
        """The trust the record is stored with. **Joined, so it can only move toward taint.**

        Evidence that does not exist is refused rather than ignored: a candidate citing
        utterances nobody said is not weakly grounded, it is ungrounded (§4, "assertion_mode
        の検証"). Once written, the citation is allowed to dangle — the episode expires and
        the belief does not.
        """
        levels = [candidate.trust_level, taint(candidate.provenance_class)]
        if candidate.evidence_ref:
            wanted = tuple(dict.fromkeys(candidate.evidence_ref))
            placeholders = ", ".join("?" * len(wanted))
            rows = conn.execute(
                f"SELECT id, trust_level FROM utterances WHERE id IN ({placeholders})", wanted
            ).fetchall()
            found = {str(row[0]): TrustLevel(str(row[1])) for row in rows}
            missing = [utterance_id for utterance_id in wanted if utterance_id not in found]
            if missing and require_evidence:
                raise MemoryRejected(f"Evidence does not exist: {', '.join(missing)}")
            levels.extend(found.values())

        trust = join_all(levels)
        return trust, provenance_from(trust, [candidate.provenance_class])
