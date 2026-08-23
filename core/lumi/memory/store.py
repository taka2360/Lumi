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

## Superseding writes two rows and edits one column

The new belief is inserted, the old row's `superseded_by` is pointed at it, and **an
episodic note about the disagreement is written in the same transaction** — not by the
caller, because a rule that depends on every caller remembering it is one that eventually
produces a silent overwrite (docs/architecture/memory.md §6).
"""

from __future__ import annotations

import asyncio
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
from lumi.provenance import (
    ProvenanceClass,
    TrustLevel,
    join,
    join_all,
    propagate_from_trust,
    taint,
)
from lumi.storage.sqlite import Database, one

log = lumi_logging.get_logger(__name__)

#: Assertion modes that claim the conversation as their source, and therefore have to name
#: at least one utterance. `self_generated` and `external` did not come from one.
GROUNDED_IN_CONVERSATION: Final = (AssertionMode.USER_STATED, AssertionMode.INFERRED)

_COLUMNS: Final = (
    "id, type, subject, content, assertion_mode, confidence, provenance_class,"
    " trust_level, base_salience, created_at, last_accessed, access_count,"
    " archived_at, valid_from, superseded_by, embedding_model_id"
)


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
        return await asyncio.to_thread(self._write_blocking, candidate, moment)

    def _write_blocking(self, candidate: MemoryCandidate, now: datetime) -> MemoryRecord:
        with self._db.transaction() as conn:
            return self._insert(conn, candidate, now)

    async def supersede(
        self, old_id: str, candidate: MemoryCandidate, *, now: datetime | None = None
    ) -> Reconciled:
        """Replace a belief **without overwriting it.** The old row stays as it was."""
        moment = now or datetime.now(UTC)
        return await asyncio.to_thread(self._supersede_blocking, old_id, candidate, moment)

    def _supersede_blocking(
        self, old_id: str, candidate: MemoryCandidate, now: datetime
    ) -> Reconciled:
        with self._db.transaction() as conn:
            existing = self._read(conn, old_id)
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
        return await asyncio.to_thread(self._reconcile_blocking, candidate, moment)

    def _reconcile_blocking(self, candidate: MemoryCandidate, now: datetime) -> Reconciled:
        content = normalize(candidate.content)
        with self._db.transaction() as conn:
            for record in self._live_rows(conn, candidate.subject, candidate.type):
                if normalize(record.content) == content:
                    # **Saying it again is not a second belief.** It is a reason to hold
                    # the one already there a little more firmly.
                    self._touch_in(conn, (record.id,), now)
                    refreshed = self._read(conn, record.id)
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
                return Reconciled(
                    record=self._insert(conn, candidate, now), resolution=Resolution.NEW
                )

            existing = conflicts[0]
            if resolve(existing, candidate) is Resolution.SUPERSEDE:
                return self._supersede_in(conn, existing, candidate, now)
            # **Kept, not dropped.** A weaker claim that turns out to be right should still
            # be findable; it just does not get to outrank what the user said.
            weakened = replace(candidate, confidence=candidate.confidence * WEAK_CONFIDENCE_FACTOR)
            return Reconciled(
                record=self._insert(conn, weakened, now), resolution=Resolution.KEEP_WEAK
            )

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
        return await asyncio.to_thread(self._get_blocking, memory_id)

    def _get_blocking(self, memory_id: str) -> MemoryRecord | None:
        with self._db.transaction() as conn:
            return self._read(conn, memory_id)

    async def live(self, subject: str) -> Sequence[MemoryRecord]:
        """Beliefs about `subject` that are neither superseded nor archived."""
        return await asyncio.to_thread(self._live_blocking, subject, None)

    async def find_conflicts(self, subject: str, content: str) -> Sequence[MemoryRecord]:
        """Live semantic beliefs about the same subject that say something else.

        **Only semantic memories conflict.** Two episodic records of different moments are
        not a disagreement; they are two things that happened.

        Whether two *differently worded* sentences mean the same thing needs embeddings
        (2e). Until then this compares text: it **cannot** tell, as opposed to having
        checked and found nothing.
        """
        records = await asyncio.to_thread(self._live_blocking, subject, MemoryType.SEMANTIC)
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
        return await asyncio.to_thread(self._recent_blocking, limit)

    def _recent_blocking(self, limit: int) -> list[MemoryRecord]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories"
                " WHERE superseded_by IS NULL AND archived_at IS NULL"
                " ORDER BY valid_from DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._hydrate(conn, row) for row in rows]

    async def browse(
        self,
        *,
        query: str = "",
        include_history: bool = False,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[MemoryRecord], int]:
        """A page of memories for the memory window, and how many there are in total.

        **Substring matching, not the 2e search.** Someone reading their own memory is
        looking for the sentence they remember writing, and semantic search answers a
        different question: it would return things that are *about* what they typed, in
        an order they cannot predict, with the exact match possibly not first. Retrieval
        ranks for a conversation; this lists for a person.

        `include_history` brings in superseded and archived rows — **what Lumi used to
        believe.** Off by default: the list would otherwise be dominated by corrections
        of corrections, and the current belief is what people come to check.
        """
        if limit <= 0:
            return [], 0
        return await asyncio.to_thread(
            self._browse_blocking, query.strip(), include_history, limit, max(offset, 0)
        )

    def _browse_blocking(
        self, query: str, include_history: bool, limit: int, offset: int
    ) -> tuple[list[MemoryRecord], int]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if not include_history:
            clauses.append("superseded_by IS NULL AND archived_at IS NULL")
        if query:
            # **`%` and `_` are escaped.** Typing a `%` into the search box otherwise
            # matches every memory, which reads as "the filter is broken".
            pattern = (
                "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            clauses.append("(subject LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')")
            parameters += [pattern, pattern]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._db.transaction() as conn:
            total = int(one(conn.execute(f"SELECT COUNT(*) FROM memories{where}", parameters))[0])
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories{where}"
                " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
            return [self._hydrate(conn, row) for row in rows], total

    async def get_many(self, memory_ids: Sequence[str]) -> dict[str, MemoryRecord]:
        """Several records at once, **by id, in no particular order.**"""
        if not memory_ids:
            return {}
        return await asyncio.to_thread(self._get_many_blocking, tuple(dict.fromkeys(memory_ids)))

    def _get_many_blocking(self, memory_ids: tuple[str, ...]) -> dict[str, MemoryRecord]:
        placeholders = ", ".join("?" * len(memory_ids))
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE id IN ({placeholders})", memory_ids
            ).fetchall()
            records = [self._hydrate(conn, row) for row in rows]
        return {record.id: record for record in records}

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
        return await asyncio.to_thread(self._needing_blocking, model_id, limit)

    def _needing_blocking(self, model_id: str, limit: int) -> list[MemoryRecord]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories"
                " WHERE superseded_by IS NULL AND embedding_model_id != ?"
                " ORDER BY created_at LIMIT ?",
                (model_id, limit),
            ).fetchall()
            return [self._hydrate(conn, row) for row in rows]

    async def mark_embedded(self, memory_ids: Sequence[str], model_id: str) -> None:
        """Record which model produced these vectors. **Written after the index, never
        before** — the other order claims an embedding that may not exist.
        """
        if not memory_ids:
            return
        await asyncio.to_thread(self._mark_blocking, tuple(memory_ids), model_id)

    def _mark_blocking(self, memory_ids: tuple[str, ...], model_id: str) -> None:
        with self._db.transaction() as conn:
            for memory_id in memory_ids:
                conn.execute(
                    "UPDATE memories SET embedding_model_id = ? WHERE id = ?",
                    (model_id, memory_id),
                )

    def _live_blocking(self, subject: str, kind: MemoryType | None) -> list[MemoryRecord]:
        with self._db.transaction() as conn:
            return self._live_rows(conn, subject, kind)

    def _live_rows(
        self, conn: apsw.Connection, subject: str, kind: MemoryType | None
    ) -> list[MemoryRecord]:
        sql = (
            f"SELECT {_COLUMNS} FROM memories"
            " WHERE subject = ? AND superseded_by IS NULL AND archived_at IS NULL"
        )
        parameters: list[Any] = [subject]
        if kind is not None:
            sql += " AND type = ?"
            parameters.append(kind.value)
        sql += " ORDER BY valid_from DESC, created_at DESC"
        rows = conn.execute(sql, tuple(parameters)).fetchall()
        return [self._hydrate(conn, row) for row in rows]

    # ── decay, recall and confirmation ─────────────────────────────────────

    async def touch(self, memory_ids: Sequence[str], *, now: datetime | None = None) -> None:
        """Record that these were recalled. **Feeds `access_boost`, not the content.**"""
        if not memory_ids:
            return
        moment = now or datetime.now(UTC)
        await asyncio.to_thread(self._touch_blocking, tuple(memory_ids), moment)

    def _touch_blocking(self, memory_ids: tuple[str, ...], now: datetime) -> None:
        with self._db.transaction() as conn:
            self._touch_in(conn, memory_ids, now)

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
        await asyncio.to_thread(self._archive_blocking, (memory_id,), moment)

    def _archive_blocking(self, memory_ids: tuple[str, ...], now: datetime) -> None:
        with self._db.transaction() as conn:
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
        return await asyncio.to_thread(self._archive_faded_blocking, moment)

    def _archive_faded_blocking(self, now: datetime) -> Sequence[str]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM memories"
                " WHERE archived_at IS NULL AND superseded_by IS NULL"
            ).fetchall()
            faded = [
                record.id
                for record in (self._hydrate(conn, row, evidence=False) for row in rows)
                if decay.is_faded(record, now)
            ]
            for memory_id in faded:
                conn.execute(
                    "UPDATE memories SET archived_at = ? WHERE id = ?", (now.isoformat(), memory_id)
                )
        if faded:
            log.info("memory.archived", count=len(faded))
        return faded

    async def confirm(self, memory_id: str, *, now: datetime | None = None) -> MemoryRecord:
        """**The only path from `tainted` to `trusted`** (Invariant 7).

        Called from the memory UI's handler and nowhere else: the user looked at this
        record and said it is right. Being confirmed also brings it back from archived —
        something the user just vouched for is not something Lumi has stopped recalling.
        """
        moment = now or datetime.now(UTC)
        return await asyncio.to_thread(self._confirm_blocking, memory_id, moment)

    def _confirm_blocking(self, memory_id: str, now: datetime) -> MemoryRecord:
        with self._db.transaction() as conn:
            existing = self._read(conn, memory_id)
            if existing is None:
                raise MemoryRejected(f"No such memory to confirm: {memory_id}")
            self._confirm_in(conn, memory_id, now)
            confirmed = self._read(conn, memory_id)
        assert confirmed is not None  # read back inside the same transaction
        log.info("memory.confirmed", memory_id=memory_id, subject=confirmed.subject)
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
        return await asyncio.to_thread(self._rewrite_blocking, memory_id, content, subject, moment)

    def _rewrite_blocking(
        self, memory_id: str, content: str, subject: str | None, now: datetime
    ) -> MemoryRecord:
        with self._db.transaction() as conn:
            existing = self._read(conn, memory_id)
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
            conn.execute(
                "UPDATE memories SET superseded_by = ? WHERE id = ?", (record.id, existing.id)
            )
            self._confirm_in(conn, record.id, now)
            rewritten = self._read(conn, record.id)
        assert rewritten is not None  # read back inside the same transaction
        log.info("memory.rewritten", old_id=existing.id, new_id=record.id, subject=record.subject)
        return rewritten

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
            f"INSERT INTO memories ({_COLUMNS}) VALUES ({', '.join('?' * 16)})",
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
        provenance = (
            ProvenanceClass.UNTRUSTED
            if candidate.provenance_class is ProvenanceClass.UNTRUSTED
            # **Never `is_raw_external` here.** A memory is a function of what Lumi was
            # told, not an observation of the outside world (docs/contracts/provenance.md).
            else propagate_from_trust(trust, is_raw_external=False)
        )
        return trust, provenance

    def _read(self, conn: apsw.Connection, memory_id: str) -> MemoryRecord | None:
        row = conn.execute(f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return None if row is None else self._hydrate(conn, row)

    def _hydrate(self, conn: apsw.Connection, row: Any, *, evidence: bool = True) -> MemoryRecord:
        """One row as a record. **Converted explicitly** — SQLite columns are typed by
        whoever last wrote them, not by the schema.
        """
        memory_id = str(row[0])
        evidence_ref: tuple[str, ...] = ()
        sources: tuple[str, ...] = ()
        if evidence:
            evidence_ref = tuple(
                str(item[0])
                for item in conn.execute(
                    "SELECT utterance_id FROM memory_evidence WHERE memory_id = ?"
                    " ORDER BY utterance_id",
                    (memory_id,),
                ).fetchall()
            )
            sources = tuple(
                str(item[0])
                for item in conn.execute(
                    "SELECT episode_id FROM memory_sources WHERE memory_id = ? ORDER BY episode_id",
                    (memory_id,),
                ).fetchall()
            )
        return MemoryRecord(
            id=memory_id,
            type=MemoryType(str(row[1])),
            subject=str(row[2]),
            content=str(row[3]),
            assertion_mode=AssertionMode(str(row[4])),
            evidence_ref=evidence_ref,
            confidence=float(row[5]),
            provenance_class=ProvenanceClass(str(row[6])),
            trust_level=TrustLevel(str(row[7])),
            base_salience=float(row[8]),
            created_at=datetime.fromisoformat(str(row[9])),
            last_accessed=datetime.fromisoformat(str(row[10])),
            access_count=int(str(row[11])),
            archived_at=None if row[12] is None else datetime.fromisoformat(str(row[12])),
            valid_from=datetime.fromisoformat(str(row[13])),
            superseded_by=None if row[14] is None else str(row[14]),
            source_episode_ids=sources,
            embedding_model_id=str(row[15]),
        )
