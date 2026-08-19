"""Permission Kernel — **the sole owner of permission decisions.**

> **Absolute rule: only Core makes permission decisions. Extension, Stage, Shell,
> Tool, and the LLM never decide** (Invariant 1, 2).

Design → docs/architecture/permission.md

## PermissionKernel knows nothing about Tools

The only things it receives as arguments are `PermissionSpec`, `SecurityScope`, and
strings. If the Kernel knew about individual tools, **the Kernel would change every
time a tool is added** (a prohibition from docs/architecture/core.md §4).
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
    """The result of a decision. **Returned along with what was written to the audit log** (used by
    the Inspector and the permission UI).
    """

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
        """Runs through `decide()`, and **records it to the audit log either way.**"""
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
        # The audit log is read back by things other than Python. **An unparsable record
        # is an absent record**, and failing here at least fails closed on the tool call
        allow_nan=False,
    )
