"""Permission Kernel — **権限判断の唯一の所有者。**

> **絶対原則: 権限判断は Core だけが行う。Extension・Stage・Shell・Tool・LLM は判断しない**
> （Invariant 1, 2）。

設計 → docs/architecture/permission.md

## PermissionKernel は Tool を知らない

引数で受け取るのは `PermissionSpec` と `SecurityScope` と文字列だけである。
Kernel が個別ツールを知ると、**ツール追加のたびに Kernel が変わる**
（docs/architecture/core.md §4 の禁止事項）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from lumi import logging as lumi_logging
from lumi.kernel.activity import Actor
from lumi.kernel.ids import ActivityId, CorrelationId
from lumi.permission.audit import AuditLog, AuditRecord
from lumi.permission.grants import Grant, GrantStore
from lumi.permission.policy import POLICY_VERSION, Decision, PermissionSpec, decide_with_rule
from lumi.permission.scope import SecurityScope
from lumi.provenance import TrustLevel

log = lumi_logging.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Authorization:
    """判断の結果。**監査に何を書いたかまで含めて返す**（Inspector と権限 UI が使う）。"""

    decision: Decision
    rule_id: str
    grant: Grant | None


class PermissionKernel:
    __slots__ = ("_audit", "_clock", "_grants")

    def __init__(
        self,
        grants: GrantStore,
        audit: AuditLog,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._grants = grants
        self._audit = audit
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    async def authorize(
        self,
        *,
        spec: PermissionSpec,
        scope: SecurityScope,
        actor: Actor,
        effective_trust: TrustLevel,
        tool: str,
        activity_id: ActivityId,
        correlation_id: CorrelationId,
        raw_input_digest: str,
        args_digest: str,
    ) -> Authorization:
        """`decide()` を通し、**決定内容にかかわらず監査に残す。**"""
        now = self._clock()
        grant = self._grants.find(spec.capability, scope, now)
        decision, rule_id = decide_with_rule(spec.risk, actor, effective_trust, grant)

        await self._audit.append(
            AuditRecord(
                ts=now,
                actor=actor,
                activity_id=activity_id,
                correlation_id=correlation_id,
                capability=spec.capability,
                security_scope_json=_scope_json(scope),
                raw_input_digest=raw_input_digest,
                decision=decision,
                reason=rule_id,
                policy_version=POLICY_VERSION,
                policy_rule_id=rule_id,
                grant_id=grant.id if grant else None,
                tool=tool,
                args_digest=args_digest,
                trust_level=effective_trust,
            )
        )

        if decision is not Decision.ALLOW:
            log.info(
                "permission.refused",
                tool=tool,
                capability=spec.capability,
                decision=decision.value,
                rule=rule_id,
                actor=actor.value,
                trust=effective_trust.value,
            )
        return Authorization(decision=decision, rule_id=rule_id, grant=grant)


def _scope_json(scope: SecurityScope) -> str:
    return json.dumps(
        {"lane": scope.lane.value, "canonical": scope.canonical, "metadata": dict(scope.metadata)},
        ensure_ascii=False,
        sort_keys=True,
    )
