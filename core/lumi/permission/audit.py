"""Audit log — **records every entry regardless of the decision.**

Schema and rationale → docs/architecture/permission.md §7

## What append-only precisely means

> **It means "cannot be tampered with or deleted through any of Lumi's Tool paths."**

Tampering from a separate process with OS admin rights can't be prevented. **That's
outside the threat model, and this never claims otherwise**
(docs/contracts/security-boundaries.md).

| Phase | Implementation |
|---|---|
| **1** | Unreachable via any Tool path. **No `DELETE` / `UPDATE` exists anywhere in the code** |
| 4a | Hash chain (`prev_hash` / `record_hash`). **Detects** tampering (doesn't prevent it) |

## Why digests are kept

Keeping both the pre-normalization digest (`raw_input_digest`) and the post-
normalization value (`security_scope_json`) makes it possible to **later verify
whether normalization was correct.** Digests are used instead of raw values so secrets
never end up in the log.
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
    """**Immutable.** If an audit record could be rewritten, it wouldn't be an audit."""

    ts: datetime
    actor: Actor
    activity_id: ActivityId
    correlation_id: CorrelationId
    capability: str
    #: The target **after** normalization
    security_scope_json: str
    #: Digest of the input **before** normalization (the raw value is never kept)
    raw_input_digest: str
    decision: Decision
    reason: str
    # : **Required.** Policy changes over time. Without knowing the rule in effect at the time, "why
    # was this allowed" can't be answered
    policy_version: str
    policy_rule_id: str
    tool: str
    args_digest: str
    grant_id: GrantId | None = None
    result_digest: str | None = None
    provenance_class: ProvenanceClass | None = None
    trust_level: TrustLevel | None = None


class AuditLog(Protocol):
    """Implemented by `lumi.storage.audit` (a Protocol to invert the dependency direction)."""

    async def append(self, record: AuditRecord) -> None: ...
