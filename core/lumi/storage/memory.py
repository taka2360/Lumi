"""The memory database: Episodes and the utterances they are made of.

Design → docs/architecture/memory.md / Privacy → docs/contracts/privacy.md §2 (rows 1-4)

**Phase 2 is the first time a conversation is written down.** Up to Phase 1, what the
user said lived in Working Memory and disappeared with the process. That is the change
this file makes, and it is why everything about it is spelled out in the privacy contract
before any of it was written.

## Episodes are the raw log. Memory records are not

| | What it is | Lifetime |
|---|---|---|
| **Episode / utterance** | What was actually said, verbatim | **90 days**, then deleted |
| Memory record | What Lumi took from it | decays and is archived, **never expires** |

**"Forgetting" and "being deleted" are not the same mechanism** (privacy.md §4). Deleting
the log a belief came from does not delete the belief; a memory record whose salience has
decayed is still on disk and can still be recovered by a strong enough cue.

## What is stored per utterance, and why provenance travels with it

Trust is a property of where a sentence came from, and **no later processing may raise
it** (Invariant 7). Storing `trust_level` next to the text is what lets a Reflection Job
in a week's time still know that a given line came from a web page rather than from the
user. Recomputing it at read time would mean guessing.

## Memory records live here too, and outlive the log

Migration 2 adds `memories` and the two tables naming what a record was made from. The
rows a record points at **are not foreign keys**: episodes expire, records do not, and a
foreign key would force one of the two wrong outcomes — an episode that cannot be deleted,
or a belief deleted along with the conversation it came from. A dangling reference here is
expected, and reading a record does not depend on resolving it.

The row-to-record mapping is `lumi.memory.store`; what lives here is the schema, because
**one file's schema has one version**, and splitting the DDL across modules is how a file
ends up half-migrated by whichever module happened to open it.

## Not here yet

Vectors and the FTS index are Phase 2e. The vector table in particular **cannot be created
before the embedding model is chosen**: `vec0` fixes the dimension at creation, and picking
768 or 1024 now would be inventing the answer to a question docs/architecture/memory.md §2
leaves open until it has been measured.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from lumi import logging as lumi_logging
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.sqlite import Database, Schema, one

log = lumi_logging.get_logger(__name__)

#: The memory database. Episodes are kept 90 days by default (`lumi.storage.retention`);
#: memory records will not be, because forgetting is not deletion.
MEMORY_SCHEMA: Schema = Schema(
    component="storage.memory",
    migrations=(
        (
            """
            CREATE TABLE episodes (
                id         TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at   TEXT
            )
            """,
            # Retention deletes by age; without the index that is a scan of every episode.
            "CREATE INDEX episodes_by_started_at ON episodes (started_at)",
            """
            CREATE TABLE utterances (
                id               TEXT    PRIMARY KEY,
                episode_id       TEXT    NOT NULL REFERENCES episodes (id) ON DELETE CASCADE,
                turn_index       INTEGER NOT NULL,
                speaker          TEXT    NOT NULL,
                text             TEXT    NOT NULL,
                provenance_class TEXT    NOT NULL,
                trust_level      TEXT    NOT NULL,
                occurred_at      TEXT    NOT NULL,
                correlation_id   TEXT,
                UNIQUE (episode_id, turn_index)
            )
            """,
            "CREATE INDEX utterances_by_episode ON utterances (episode_id, turn_index)",
        ),
        (
            # **What Lumi took from the conversation**, as opposed to what was said.
            # `superseded_by` is what makes a belief versioned rather than overwritten
            # (docs/architecture/memory.md §6): the old row stays exactly as it was.
            """
            CREATE TABLE memories (
                id                 TEXT    PRIMARY KEY,
                type               TEXT    NOT NULL,
                subject            TEXT    NOT NULL,
                content            TEXT    NOT NULL,
                assertion_mode     TEXT    NOT NULL,
                confidence         REAL    NOT NULL,
                provenance_class   TEXT    NOT NULL,
                trust_level        TEXT    NOT NULL,
                base_salience      REAL    NOT NULL,
                created_at         TEXT    NOT NULL,
                last_accessed      TEXT    NOT NULL,
                access_count       INTEGER NOT NULL DEFAULT 0,
                archived_at        TEXT,
                valid_from         TEXT    NOT NULL,
                superseded_by      TEXT    REFERENCES memories (id),
                embedding_model_id TEXT    NOT NULL DEFAULT ''
            )
            """,
            # Conflict detection asks for the live beliefs about one subject, which is
            # every read on the write path.
            "CREATE INDEX memories_by_subject ON memories (subject, valid_from)",
            "CREATE INDEX memories_by_superseded ON memories (superseded_by)",
            # **No foreign key to `utterances`.** See the module docstring: the evidence
            # expires with the episode and the belief stays.
            """
            CREATE TABLE memory_evidence (
                memory_id    TEXT NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
                utterance_id TEXT NOT NULL,
                PRIMARY KEY (memory_id, utterance_id)
            )
            """,
            """
            CREATE TABLE memory_sources (
                memory_id  TEXT NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
                episode_id TEXT NOT NULL,
                PRIMARY KEY (memory_id, episode_id)
            )
            """,
        ),
    ),
)

#: Who said it. **Not an identity**: STT cannot tell the user from someone else in
#: the room, and Lumi does not pretend otherwise (docs/contracts/privacy.md §6).
SPEAKER_USER: Final = "user"
SPEAKER_LUMI: Final = "lumi"


@dataclass(frozen=True, slots=True)
class Utterance:
    """One line of a conversation, as it was said.

    **Immutable.** An episode is a record of what happened; editing it would make it a
    record of what someone later preferred to have happened.
    """

    id: str
    episode_id: str
    turn_index: int
    speaker: str
    text: str
    provenance_class: ProvenanceClass
    trust_level: TrustLevel
    occurred_at: datetime
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class Episode:
    """One conversation. Open until `close` is called for it."""

    id: str
    session_id: str
    started_at: datetime
    ended_at: datetime | None = None


class EpisodeStore:
    """Writes and reads the conversation log. **Blocking I/O runs off the event loop.**

    This class does not delete. Retention and "erase everything" do, from
    `lumi.storage.retention`, so that every path which removes user data is in one file
    (docs/contracts/privacy.md §5).
    """

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    async def open_episode(self, episode: Episode) -> None:
        await asyncio.to_thread(self._open_blocking, episode)

    def _open_blocking(self, episode: Episode) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO episodes (id, session_id, started_at, ended_at)"
                " VALUES (?, ?, ?, NULL)",
                (episode.id, episode.session_id, episode.started_at.isoformat()),
            )

    async def close_episode(self, episode_id: str, ended_at: datetime) -> None:
        """Marks the conversation finished. **A missing episode is not an error** — a
        session that never said anything never opened one.
        """
        await asyncio.to_thread(self._close_blocking, episode_id, ended_at)

    def _close_blocking(self, episode_id: str, ended_at: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE episodes SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (ended_at.isoformat(), episode_id),
            )

    async def episode(self, episode_id: str) -> Episode | None:
        return await asyncio.to_thread(self._episode_blocking, episode_id)

    def _episode_blocking(self, episode_id: str) -> Episode | None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT id, session_id, started_at, ended_at FROM episodes WHERE id = ?",
                (episode_id,),
            ).fetchone()
        if row is None:
            return None
        return Episode(
            id=str(row[0]),
            session_id=str(row[1]),
            started_at=datetime.fromisoformat(str(row[2])),
            ended_at=None if row[3] is None else datetime.fromisoformat(str(row[3])),
        )

    async def append(self, utterance: Utterance) -> None:
        await asyncio.to_thread(self._append_blocking, utterance)

    def _append_blocking(self, utterance: Utterance) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO utterances"
                " (id, episode_id, turn_index, speaker, text, provenance_class,"
                "  trust_level, occurred_at, correlation_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    utterance.id,
                    utterance.episode_id,
                    utterance.turn_index,
                    utterance.speaker,
                    utterance.text,
                    utterance.provenance_class.value,
                    utterance.trust_level.value,
                    utterance.occurred_at.isoformat(),
                    utterance.correlation_id,
                ),
            )

    async def utterances(self, episode_id: str) -> Sequence[Utterance]:
        return await asyncio.to_thread(self._utterances_blocking, episode_id)

    def _utterances_blocking(self, episode_id: str) -> Sequence[Utterance]:
        with self._db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, episode_id, turn_index, speaker, text, provenance_class,"
                " trust_level, occurred_at, correlation_id"
                " FROM utterances WHERE episode_id = ? ORDER BY turn_index",
                (episode_id,),
            ).fetchall()
        # **Converted explicitly.** SQLite columns are dynamically typed, so a row is
        # only `str` because the writer put one there; saying so is cheaper than being
        # surprised by it in a `datetime.fromisoformat` two weeks from now.
        return [
            Utterance(
                id=str(row[0]),
                episode_id=str(row[1]),
                turn_index=int(str(row[2])),
                speaker=str(row[3]),
                text=str(row[4]),
                provenance_class=ProvenanceClass(str(row[5])),
                trust_level=TrustLevel(str(row[6])),
                occurred_at=datetime.fromisoformat(str(row[7])),
                correlation_id=None if row[8] is None else str(row[8]),
            )
            for row in rows
        ]

    async def count(self) -> int:
        return await asyncio.to_thread(self._count_blocking)

    def _count_blocking(self) -> int:
        with self._db.transaction() as conn:
            return int(one(conn.execute("SELECT COUNT(*) FROM utterances"))[0])
