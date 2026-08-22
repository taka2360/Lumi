"""SQLite implementation of `AuditLog`, and the audit database's schema.

**Never write `DELETE` or `UPDATE` in this file.** That is what "append-only" means
(docs/contracts/security-boundaries.md): unreachable from every Tool path, and therefore
from the LLM, from autonomous behaviour and from Extensions.

**Deletion is not absent, it is elsewhere.** The user may erase their own audit log —
"Lumi cannot erase its own tracks" and "the user cannot erase theirs" are different
claims, and only the first is an Invariant. Retention and "erase everything" delete from
`lumi.storage.retention`, and nowhere else does (docs/contracts/privacy.md §5).
"""

from __future__ import annotations

import asyncio

from lumi.permission.audit import AuditRecord
from lumi.storage.sqlite import Database, Schema

#: The audit database. Kept 180 days by default; **the deletion record is kept forever**
#: and is not part of "erase everything" (docs/contracts/privacy.md §2, rows 6 and 10).
AUDIT_SCHEMA: Schema = Schema(
    component="storage.audit",
    migrations=(
        (
            # `prev_hash` / `record_hash` are added as a migration in Phase 4a.
            # They aren't added now because **an unused column is never added "for the future."**
            """
            CREATE TABLE audit_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                  TEXT NOT NULL,
                actor               TEXT NOT NULL,
                activity_id         TEXT NOT NULL,
                correlation_id      TEXT NOT NULL,
                capability          TEXT NOT NULL,
                security_scope_json TEXT NOT NULL,
                raw_input_digest    TEXT NOT NULL,
                decision            TEXT NOT NULL,
                reason              TEXT NOT NULL,
                policy_version      TEXT NOT NULL,
                policy_rule_id      TEXT NOT NULL,
                grant_id            TEXT,
                tool                TEXT NOT NULL,
                args_digest         TEXT NOT NULL,
                result_digest       TEXT,
                provenance_class    TEXT,
                trust_level         TEXT
            )
            """,
            "CREATE INDEX audit_by_activity ON audit_log (activity_id)",
            "CREATE INDEX audit_by_ts ON audit_log (ts)",
        ),
        (
            # **What deletion removed, never what it removed.** No text, no digests: a
            # digest of a deleted utterance is still a fact about that utterance, and
            # "erase everything" that leaves fingerprints behind is not erasure
            # (docs/contracts/privacy.md §5).
            """
            CREATE TABLE deletion_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT    NOT NULL,
                target  TEXT    NOT NULL,
                count   INTEGER NOT NULL,
                trigger TEXT    NOT NULL
            )
            """,
            "CREATE INDEX deletion_by_ts ON deletion_log (ts)",
        ),
    ),
)


class SqliteAuditLog:
    """Implementation of `lumi.permission.audit.AuditLog`."""

    __slots__ = ("_db",)

    def __init__(self, database: Database) -> None:
        self._db = database

    async def append(self, record: AuditRecord) -> None:
        await asyncio.to_thread(self._append_blocking, record)

    def _append_blocking(self, record: AuditRecord) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log"
                " (ts, actor, activity_id, correlation_id, capability, security_scope_json,"
                "  raw_input_digest, decision, reason, policy_version, policy_rule_id,"
                "  grant_id, tool, args_digest, result_digest, provenance_class, trust_level)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.ts.isoformat(),
                    record.actor.value,
                    str(record.activity_id),
                    str(record.correlation_id),
                    record.capability,
                    record.security_scope_json,
                    record.raw_input_digest,
                    record.decision.value,
                    record.reason,
                    record.policy_version,
                    record.policy_rule_id,
                    str(record.grant_id) if record.grant_id else None,
                    record.tool,
                    record.args_digest,
                    record.result_digest,
                    record.provenance_class.value if record.provenance_class else None,
                    record.trust_level.value if record.trust_level else None,
                ),
            )
