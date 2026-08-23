"""Retention — **the only code that deletes what the user said.**

Policy → docs/contracts/privacy.md §4 and §5 / Decision → ADR-038

Every `DELETE` against a table holding user data lives in this file. That is not tidiness:
docs/contracts/privacy.md §5 draws the boundary for the audit log as "unreachable from
every Tool path, reachable from retention and from erase-everything, and nowhere else,"
and a boundary spread across four modules is one nobody can check.

## Why there is a deadline at all

"We never delete anything; the user can if they want to" sounds like the respectful
choice, and **in practice nobody ever does.** Three years of conversation nobody has read,
whose contents the user could not describe, is not a promise kept — it is an accident
built out of one. So the default is a deadline, and **unlimited is something the user
chooses**, which is what makes it mean anything.

## What a deadline does not apply to

**Memory records are not on any deadline.** Deleting "the user likes Factorio" after 90
days is not forgetting, it is destruction: forgetting is decay and archiving, and it is
recoverable (docs/architecture/memory.md §5). The Episode the belief came from expires;
the belief does not.

They can still be deleted **by the user**, one at a time, from the memory UI — and that
path is here rather than on `MemoryStore` for the same reason as everything else in this
file. A store that could delete would put a second `DELETE` against user data in a second
module, and the boundary above stops being something one file answers.

## The record of deletion outlives what it deleted

Each pass writes how many rows went, against which target, and why. **Never what they
contained** — not the text, and not a digest of it, because a digest of a deleted
utterance is still a fact about that utterance and would make "erase everything" a lie.
The record itself is kept forever and is **not** part of erase-everything: if the record
went too, there would be no way to tell that anything had been removed at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

import apsw

from lumi import logging as lumi_logging
from lumi.storage.sqlite import Database, one

log = lumi_logging.get_logger(__name__)


class Target(StrEnum):
    """A row of the table in docs/contracts/privacy.md §2. **The names are the record.**"""

    EPISODES = "episodes"
    #: The `events` table. **Named for the record, not the table**: privacy.md calls it a
    #: DomainEvent, and the deletion record is read by people, not by SQL.
    EVENTS = "domain_events"
    AUDIT = "audit_log"
    #: Row 2 of the table. **Never expires** — memory records are only ever deleted
    #: because the user deleted them.
    MEMORIES = "memory_records"


class Trigger(StrEnum):
    """Why something was deleted. **All of these are the user's own doing** — one is the
    policy they left in place, the others are something they pressed.
    """

    RETENTION = "retention"
    #: "Erase everything" from the memory window (privacy.md §5).
    ERASE = "erase"
    #: The user deleted particular memories from the memory UI.
    PURGE = "purge"


#: Defaults from docs/contracts/privacy.md §2. **That table is the definition**; these are
#: it in code, and a change belongs there first.
DEFAULT_EPISODE_DAYS: Final = 90
DEFAULT_AUDIT_DAYS: Final = 180
DEFAULT_EVENT_DAYS: Final = 30

#: What the user chooses when they want no deadline at all.
UNLIMITED: Final = None


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """How long each kind of record is kept, in days. `None` means no deadline.

    **Unlimited is a setting, not a default.** See the module docstring for why.
    """

    episode_days: int | None = DEFAULT_EPISODE_DAYS
    audit_days: int | None = DEFAULT_AUDIT_DAYS
    event_days: int | None = DEFAULT_EVENT_DAYS

    def __post_init__(self) -> None:
        """**Refused at the door.** A negative period is a cutoff in the future, which
        deletes everything; finding that out halfway through a pass means some targets
        are already gone and the rest are not.
        """
        for name in ("episode_days", "audit_days", "event_days"):
            days = getattr(self, name)
            if days is not None and days < 0:
                raise ValueError(f"Retention in days cannot be negative: {name}={days}")

    def cutoff(self, days: int | None, now: datetime) -> datetime | None:
        """The timestamp before which records of that kind expire, or `None` if never."""
        if days is None:
            return None
        if days < 0:
            raise ValueError(f"Retention in days cannot be negative: {days}")
        return now - timedelta(days=days)


@dataclass(frozen=True, slots=True)
class Deletion:
    """One target's result. **Reported even when it is zero** — "nothing expired" and
    "the pass never ran" are different facts, and only one of them is fine.
    """

    target: Target
    count: int
    trigger: Trigger


class RetentionService:
    """Applies the policy across the three databases.

    **The clock is injected.** A retention job that can only be tested by waiting 90 days
    is one that will ship untested; every test here sets `now` and asserts exactly which
    rows went (docs/contracts/privacy.md §7).
    """

    __slots__ = ("_audit", "_events", "_memory")

    def __init__(self, *, memory: Database, events: Database, audit: Database) -> None:
        self._memory = memory
        self._events = events
        self._audit = audit

    async def run(self, policy: RetentionPolicy, *, now: datetime | None = None) -> list[Deletion]:
        """Delete everything past its deadline. **Returns what went, per target.**"""
        moment = now or datetime.now(UTC)
        return await asyncio.to_thread(self._run_blocking, policy, moment)

    def _run_blocking(self, policy: RetentionPolicy, now: datetime) -> list[Deletion]:
        deletions = [
            self._expire(
                self._memory,
                Target.EPISODES,
                table="episodes",
                where="started_at < ?",
                # **Deleted explicitly, not by `ON DELETE CASCADE`.** The cascade needs
                # `PRAGMA foreign_keys` to be on, and a pragma that silently fails to
                # apply leaves utterances of episodes nobody can see — deleted from the
                # user's point of view, still on disk in fact.
                cascade=(
                    "DELETE FROM utterances WHERE episode_id IN"
                    " (SELECT id FROM episodes WHERE started_at < ?)"
                ),
                cutoff=policy.cutoff(policy.episode_days, now),
            ),
            self._expire(
                self._events,
                Target.EVENTS,
                table="events",
                where="occurred_at < ?",
                cutoff=policy.cutoff(policy.event_days, now),
            ),
            self._expire(
                self._audit,
                Target.AUDIT,
                table="audit_log",
                where="ts < ?",
                cutoff=policy.cutoff(policy.audit_days, now),
            ),
        ]
        try:
            self._record(deletions, now)
        except Exception:
            log.exception("retention.record_failed", removed=sum(d.count for d in deletions))
        return deletions

    def _expire(
        self,
        database: Database,
        target: Target,
        *,
        table: str,
        where: str,
        cutoff: datetime | None,
        cascade: str | None = None,
    ) -> Deletion:
        """One target. **A `None` cutoff deletes nothing** (the user chose unlimited).

        The count is taken as the difference across the delete, so what gets recorded is
        **how many rows actually went** rather than how many the code meant to ask for.
        """
        if cutoff is None:
            return Deletion(target=target, count=0, trigger=Trigger.RETENTION)

        boundary = cutoff.isoformat()
        count_sql = f"SELECT COUNT(*) FROM {table}"
        delete_sql = f"DELETE FROM {table} WHERE {where}"
        with database.transaction() as conn:
            before = int(one(conn.execute(count_sql))[0])
            if cascade is not None:
                conn.execute(cascade, (boundary,))
            conn.execute(delete_sql, (boundary,))
            after = int(one(conn.execute(count_sql))[0])
        return Deletion(target=target, count=before - after, trigger=Trigger.RETENTION)

    async def purge_memories(
        self, memory_ids: Sequence[str], *, now: datetime | None = None
    ) -> Deletion:
        """Delete memory records outright. **Only ever from an explicit user action**
        (docs/contracts/privacy.md §5, "個別の削除").

        This is the one deletion in Lumi that is not about a deadline, and it is
        irreversible: `archive()` is what "forget this" means, and this is what "this
        should never have been written down" means.

        **The deletion and its record are not one transaction.** They live in different
        database files, and SQLite has nothing that spans two. The order is deletion
        first, deliberately: the other order leaves "recorded as deleted, still present"
        after a crash — a user believing something is gone when it is not
        (docs/contracts/privacy.md §5).
        """
        moment = now or datetime.now(UTC)
        if not memory_ids:
            return Deletion(target=Target.MEMORIES, count=0, trigger=Trigger.PURGE)
        return await asyncio.to_thread(self._purge_blocking, tuple(memory_ids), moment)

    def _purge_blocking(self, memory_ids: tuple[str, ...], now: datetime) -> Deletion:
        placeholders = ", ".join("?" * len(memory_ids))
        with self._memory.transaction() as conn:
            before = int(one(conn.execute("SELECT COUNT(*) FROM memories"))[0])
            # **The chain of succession is cut, not followed.** A record whose successor
            # the user deleted is still a belief they hold; deleting it too would turn
            # "delete this one" into "delete the history it happens to be part of".
            conn.execute(
                f"UPDATE memories SET superseded_by = NULL WHERE superseded_by IN ({placeholders})",
                memory_ids,
            )
            # Deleted explicitly rather than by `ON DELETE CASCADE`, for the reason given
            # in `_run_blocking`: a pragma that silently fails to apply would leave rows
            # nobody can reach but which are still on disk.
            conn.execute(
                f"DELETE FROM memory_evidence WHERE memory_id IN ({placeholders})", memory_ids
            )
            conn.execute(
                f"DELETE FROM memory_sources WHERE memory_id IN ({placeholders})", memory_ids
            )
            conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", memory_ids)
            after = int(one(conn.execute("SELECT COUNT(*) FROM memories"))[0])

        deletion = Deletion(target=Target.MEMORIES, count=before - after, trigger=Trigger.PURGE)
        try:
            self._record((deletion,), now)
        except Exception:
            log.exception("retention.record_failed", removed=deletion.count)
        return deletion

    async def count_everything(self) -> list[Deletion]:
        """What "erase everything" would remove, per row of privacy.md §2.

        **Every row is reported, zero included.** "There is none of that kind of data"
        and "that kind is missing from the list" look identical once a row is dropped,
        and the second one is a bug in the thing the user is about to trust.

        The counts are a snapshot, not a promise: a reflection pass may write another
        memory between the preview and the press. It is close enough for consent about
        *what kinds* of data go, which is what the confirmation is for.
        """
        return await asyncio.to_thread(self._count_blocking)

    def _count_blocking(self) -> list[Deletion]:
        return [
            Deletion(target=target, count=self._count(database, table), trigger=Trigger.ERASE)
            for target, database, table in self._erasable()
        ]

    def _erasable(self) -> tuple[tuple[Target, Database, str], ...]:
        """The §2 rows whose "erase everything" column is ✓. **One list, two callers** —
        the preview and the erase must not be able to disagree about what is included.
        """
        return (
            (Target.MEMORIES, self._memory, "memories"),
            (Target.EPISODES, self._memory, "episodes"),
            (Target.EVENTS, self._events, "events"),
            (Target.AUDIT, self._audit, "audit_log"),
        )

    def _count(self, database: Database, table: str) -> int:
        with database.transaction() as conn:
            return self._count_in(conn, table)

    def _count_in(self, conn: apsw.Connection, table: str) -> int:
        """A count on a connection the caller already has. **Used inside the erase's own
        transaction**, so the number describes the rows that transaction is deleting.
        """
        return int(one(conn.execute(f"SELECT COUNT(*) FROM {table}"))[0])

    async def erase_everything(self, *, now: datetime | None = None) -> list[Deletion]:
        """**Erase everything the user's data amounts to** (privacy.md §5).

        Deletes the rows of every §2 row marked "erase everything", including the audit
        log — which is append-only *to Lumi*, not to the person whose log it is. The one
        thing that survives is `deletion_log`: a record that "everything was erased" is
        not the data, and losing it would make the erasure itself unverifiable.

        **The derived indexes go too.** `memory_fts` holds the text of every memory, so
        leaving it behind would leave the sentences readable after the memories they came
        from are gone. They are excluded from ordinary deletion because they are rebuilt
        from the rows; here there are no rows left to rebuild from.
        """
        moment = now or datetime.now(UTC)
        return await asyncio.to_thread(self._erase_blocking, moment)

    def _erase_blocking(self, now: datetime) -> list[Deletion]:
        # **Counted inside the transaction that deletes.** Counting first and deleting
        # after leaves a window in which a reflection pass writes a memory: it is deleted
        # like everything else, and the recorded number says it never existed. The record
        # of a deletion has to describe the deletion that happened.
        counts: dict[Target, int] = {}

        with self._memory.transaction() as conn:
            counts[Target.MEMORIES] = self._count_in(conn, "memories")
            counts[Target.EPISODES] = self._count_in(conn, "episodes")
            for table in (
                "memory_evidence",
                "memory_sources",
                "memory_vectors",
                "memory_fts",
                "memories",
                "utterances",
                "episodes",
            ):
                conn.execute(f"DELETE FROM {table}")
            # **Verified before the transaction closes.** A table that refused to empty is
            # reported as what it is, rather than as the number this meant to remove.
            counts[Target.MEMORIES] -= self._count_in(conn, "memories")
            counts[Target.EPISODES] -= self._count_in(conn, "episodes")
        with self._events.transaction() as conn:
            counts[Target.EVENTS] = self._count_in(conn, "events")
            conn.execute("DELETE FROM events")
            counts[Target.EVENTS] -= self._count_in(conn, "events")
        with self._audit.transaction() as conn:
            counts[Target.AUDIT] = self._count_in(conn, "audit_log")
            conn.execute("DELETE FROM audit_log")
            counts[Target.AUDIT] -= self._count_in(conn, "audit_log")

        deletions = [
            Deletion(target=target, count=counts[target], trigger=Trigger.ERASE)
            for target, _database, _table in self._erasable()
        ]
        try:
            self._record(deletions, now)
        except Exception:
            log.exception("retention.record_failed", removed=sum(d.count for d in deletions))
        return deletions

    def _record(self, deletions: Sequence[Deletion], now: datetime) -> None:
        """Write what was removed into the audit database.

        **Only the counts.** The content is exactly what was being deleted, and a record
        that keeps a shadow of it is not a record of deletion.

        **A failure here does not turn a completed deletion into a failed pass.** The rows
        are already gone; reporting the pass as failed would leave the caller believing
        they are still there, which is the more dangerous of the two wrong beliefs. It is
        logged at error level, because a deletion nobody recorded is exactly what §5 says
        must not happen quietly.
        """
        timestamp = now.isoformat()
        with self._audit.transaction() as conn:
            for deletion in deletions:
                if deletion.count == 0:
                    # **Zero is not written.** A row per target per pass would bury the
                    # passes that actually removed something.
                    continue
                conn.execute(
                    "INSERT INTO deletion_log (ts, target, count, trigger) VALUES (?, ?, ?, ?)",
                    (timestamp, deletion.target.value, deletion.count, deletion.trigger.value),
                )
        for deletion in deletions:
            if deletion.count:
                log.info(
                    "retention.deleted",
                    target=deletion.target.value,
                    count=deletion.count,
                    trigger=deletion.trigger.value,
                )
