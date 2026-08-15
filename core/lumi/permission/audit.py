"""監査ログ — **決定内容にかかわらず全件記録する。**

スキーマと根拠 → docs/architecture/permission.md §7

## append-only の正確な意味

> **「Lumi の全 Tool 経路から改竄・削除できない」という意味である。**

OS 管理者権限を持つ別プロセスからの改竄は防げない。**それは脅威モデル外であり、
防げると書かない**（docs/contracts/security-boundaries.md）。

| Phase | 実装 |
|---|---|
| **1** | Tool 経路からの到達不能性。**`DELETE` / `UPDATE` がコードベースに存在しない** |
| 4a | hash chain（`prev_hash` / `record_hash`）。改竄の**検出**（防止ではない） |

## なぜ digest を残すのか

正規化前（`raw_input_digest`）と後（`security_scope_json`）の両方を残すと、
**「正規化が正しかったか」を後から検証できる。** ダイジェストにするのは、
機密情報をログに残さないため。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from lumi.kernel.activity import Actor
from lumi.kernel.ids import ActivityId, CorrelationId
from lumi.permission.grants import GrantId
from lumi.permission.policy import Decision
from lumi.provenance import ProvenanceClass, TrustLevel


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """**不変。** 監査記録が書き換えられるなら、それは監査ではない。"""

    ts: datetime
    actor: Actor
    activity_id: ActivityId
    correlation_id: CorrelationId
    capability: str
    #: 正規化**後**の対象
    security_scope_json: str
    #: 正規化**前**の入力のダイジェスト（生の値は残さない）
    raw_input_digest: str
    decision: Decision
    reason: str
    #: **必須。** Policy は将来変わる。当時のルールが分からないと「なぜ許可したか」に答えられない
    policy_version: str
    policy_rule_id: str
    tool: str
    args_digest: str
    grant_id: GrantId | None = None
    result_digest: str | None = None
    provenance_class: ProvenanceClass | None = None
    trust_level: TrustLevel | None = None


class AuditLog(Protocol):
    """実装は `lumi.storage.audit`（依存の向きを逆にするための Protocol）。"""

    async def append(self, record: AuditRecord) -> None: ...
