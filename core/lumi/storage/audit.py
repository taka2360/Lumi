"""`AuditLog` の SQLite 実装。

**このファイルに `DELETE` も `UPDATE` も書かない。** それが Phase 1 における
append-only の実装である（docs/contracts/security-boundaries.md）。
静的検査で「コードベース全体に audit_log への `DELETE` / `UPDATE` が存在しない」ことを縛る。
"""

from __future__ import annotations

import asyncio

from lumi.permission.audit import AuditRecord
from lumi.storage.sqlite import Database


class SqliteAuditLog:
    """`lumi.permission.audit.AuditLog` の実装。"""

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
