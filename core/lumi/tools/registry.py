"""Tool Registry — **the single place that enforces the Kernel execution contract.**

Contract → docs/contracts/tool-execution.md

```
canonicalize → decide → bind → verify → execute
```

> **The contract: the target Policy inspected and the target execute operated on must
> be identical.**

## fail-closed

A Tool that doesn't satisfy the conditions at registration time **raises an exception
at startup.** Failing at registration is a stronger form of fail-closed than deciding
this on every call at runtime (docs/architecture/permission.md §3.1).

## Three-stage recording

`INTENT_RECORDED` (bind/verify already done) → `EXECUTION_STARTED` →
`EXECUTION_CONFIRMED`. **Phase 1 only records these. Recovery is Phase 4a**
(docs/architecture/recovery.md).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from lumi import logging as lumi_logging
from lumi.kernel.cancellation import Cancellation
from lumi.kernel.event import EventBus
from lumi.kernel.hooks import HookName, HookRegistry, Veto
from lumi.kernel.recovery import ToolLifecycleEvent, digest, idempotency_key, tool_lifecycle_draft
from lumi.permission.kernel import PermissionKernel
from lumi.permission.policy import Decision, Risk, SideEffect
from lumi.permission.scope import (
    LANE_CLASS,
    LANE_RESULT_IS_EXTERNAL,
    Handle,
    ScopeLane,
    SecurityScope,
    ToolClass,
)
from lumi.permission.verifiers import (
    BindVerificationError,
    BindVerifier,
    CanonicalizationError,
    Canonicalizer,
    ResultVerifier,
)
from lumi.provenance import ProvenanceClass, TrustLevel, propagate_from_trust, taint
from lumi.tools.base import Tool, ToolContext, ToolDescriptor, ToolError, ToolResult

log = lumi_logging.get_logger(__name__)


class ToolState(StrEnum):
    """**Transitions independently of Activity state** (docs/contracts/state-machines.md).

    Only the Tool Registry may execute transitions. A Tool itself holds no state.
    """

    AUTHORIZED = "authorized"
    DENIED = "denied"
    BOUND = "bound"
    BIND_FAILED = "bind_failed"
    EXECUTING = "executing"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    #: **Not a "failure."** An intent exists but no confirmation (outcome unknown)
    UNKNOWN = "unknown"


class ToolRegistrationError(RuntimeError):
    """Failed fail-closed verification at registration time. **Halts startup.**"""


class ToolRegistry:
    __slots__ = (
        "_bind_verifiers",
        "_bus",
        "_canonicalizers",
        "_hooks",
        "_permission",
        "_result_verifiers",
        "_tools",
    )

    def __init__(
        self,
        permission: PermissionKernel,
        bus: EventBus,
        hooks: HookRegistry,
        *,
        canonicalizers: Mapping[ScopeLane, Canonicalizer] | None = None,
        bind_verifiers: Mapping[ScopeLane, BindVerifier] | None = None,
        result_verifiers: Mapping[ScopeLane, ResultVerifier] | None = None,
    ) -> None:
        self._permission = permission
        self._bus = bus
        self._hooks = hooks
        self._canonicalizers: dict[ScopeLane, Canonicalizer] = dict(canonicalizers or {})
        self._bind_verifiers: dict[ScopeLane, BindVerifier] = dict(bind_verifiers or {})
        self._result_verifiers: dict[ScopeLane, ResultVerifier] = dict(result_verifiers or {})
        self._tools: dict[str, Tool] = {}

    # ── Registration ──────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        """A Class A (in-core) Tool. **Raises if it fails to satisfy the conditions.**"""
        if not tool.name or not tool.description:
            raise ToolRegistrationError("メタデータが欠けている")
        if tool.name in self._tools:
            raise ToolRegistrationError(f"{tool.name}: 名前が重複している")

        if LANE_CLASS[tool.lane] is not ToolClass.A:
            # Class B lanes are provided by out-of-process code. in-core must never
            # claim one (the Handle contract only holds for in-core → ADR-017).
            raise ToolRegistrationError(f"{tool.name}: {tool.lane} は Class B の lane")

        if tool.permission.risk is Risk.DENIED:
            # DENIED only ever appears as an "effective risk" value — it's not something a Tool can
            # declare.
            raise ToolRegistrationError(f"{tool.name}: risk に DENIED は宣言できない")

        if tool.lane not in self._canonicalizers:
            raise ToolRegistrationError(f"{tool.name}: {tool.lane} の Canonicalizer が無い")
        if tool.lane not in self._bind_verifiers:
            raise ToolRegistrationError(f"{tool.name}: {tool.lane} の BindVerifier が無い")

        if (
            tool.permission.cancellation is Cancellation.NON_CANCELLABLE
            and tool.permission.side_effect is not SideEffect.NONE
            and tool.permission.risk < Risk.L3
        ):
            # Never let an uncancellable side effect happen without user confirmation.
            raise ToolRegistrationError(
                f"{tool.name}: non_cancellable かつ副作用ありなら risk >= L3 が必要"
            )

        self._tools[tool.name] = tool

    def list_exposed(self) -> list[ToolDescriptor]:
        """Only the `deferred=False` ones. **Kept to roughly a dozen even past 50 registered
        tools.**
        """
        return [self._describe(tool) for tool in self._tools.values() if not tool.deferred]

    def search(self, query: str) -> list[ToolDescriptor]:
        """Implementation of the `tool_search` meta-tool. **Includes `deferred` ones too.**"""
        needle = query.lower()
        return [
            self._describe(tool)
            for tool in self._tools.values()
            if needle in tool.name.lower() or needle in tool.description.lower()
        ]

    def _describe(self, tool: Tool) -> ToolDescriptor:
        return ToolDescriptor(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            kind=tool.kind,
            deferred=tool.deferred,
        )

    # ── Execution ──────────────────────────────────────────────

    async def invoke(
        self, tool_name: str, ctx: ToolContext, raw_input: Mapping[str, Any]
    ) -> ToolResult:
        """**`Tool.execute` is never called from any path other than this one** (Invariant 2)."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return self._refuse(ctx, tool_name, "unknown_tool", "登録されていない")

        raw_digest = digest(_stable_json(raw_input))

        # 1. Normalization — owned by the Kernel. **Failure means deny** (fail-closed)
        try:
            scope = self._canonicalizers[tool.lane].canonicalize(raw_input)
        except CanonicalizationError as error:
            return self._refuse(ctx, tool_name, "canonicalization_failed", str(error))

        # 2. Permission decision — owned by the Kernel. The Tool has no part in this
        auth = await self._permission.authorize(
            spec=tool.permission,
            scope=scope,
            actor=ctx.actor,
            effective_trust=ctx.input_trust_level,
            tool=tool.name,
            activity_id=ctx.activity_id,
            correlation_id=ctx.correlation_id,
            raw_input_digest=raw_digest,
            args_digest=raw_digest,
        )
        if auth.decision is not Decision.ALLOW:
            # Phase 1 has no permission-prompt UI. **`ask` means "don't execute"**
            # (never execute on its own authority). The UI and Grant issuance land in Phase 4a.
            return self._refuse(ctx, tool_name, auth.decision.value, auth.rule_id)

        # 3. before_tool Hook (**can veto**)
        veto = await self._hooks.run(
            HookName.BEFORE_TOOL,
            {"tool": tool.name, "capability": tool.permission.capability, "scope": scope.canonical},
        )
        if isinstance(veto, Veto):
            return self._refuse(ctx, tool_name, "vetoed", veto.reason)

        # 4. bind — implemented by the Tool (needs domain knowledge)
        try:
            handle = tool.bind(ctx, scope)
        except Exception as error:
            log.warning("tool.bind_failed", tool=tool.name, error=str(error))
            return self._refuse(ctx, tool_name, ToolState.BIND_FAILED.value, str(error))

        # 5. verify — owned by the Kernel * the crux of the contract
        try:
            self._bind_verifiers[tool.lane].verify(scope, handle)
        except BindVerificationError as error:
            handle.close()
            log.warning("tool.verify_failed", tool=tool.name, error=str(error))
            return self._refuse(ctx, tool_name, ToolState.BIND_FAILED.value, str(error))

        return await self._execute(tool, ctx, scope, handle, raw_digest)

    async def _execute(
        self,
        tool: Tool,
        ctx: ToolContext,
        scope: SecurityScope,
        handle: Handle,
        raw_digest: str,
    ) -> ToolResult:
        key = ctx.idempotency_key or idempotency_key(
            activity_id=ctx.activity_id,
            tool_name=tool.name,
            security_scope_canonical=scope.canonical,
            args_digest=raw_digest,
        )
        await self._record(ToolLifecycleEvent.INTENT_RECORDED, tool, ctx, key)
        await self._record(ToolLifecycleEvent.EXECUTION_STARTED, tool, ctx, key)

        try:
            outcome = await tool.execute(ctx, handle)
        except Exception as error:
            await self._record(ToolLifecycleEvent.EXECUTION_ABORTED, tool, ctx, key)
            log.warning("tool.execute_failed", tool=tool.name, error=str(error))
            return self._refuse(ctx, tool.name, ToolState.FAILED.value, str(error))
        finally:
            handle.close()

        await self._record(ToolLifecycleEvent.EXECUTION_CONFIRMED, tool, ctx, key)

        # **Provenance is attached by Core.** A Tool's self-report is never trusted
        provenance_class = propagate_from_trust(
            ctx.input_trust_level, is_raw_external=LANE_RESULT_IS_EXTERNAL[tool.lane]
        )
        result = ToolResult(
            ok=outcome.ok,
            value=outcome.value,
            error=outcome.error,
            provenance_class=provenance_class,
            trust_level=taint(provenance_class),
        )
        await self._hooks.run(
            HookName.AFTER_TOOL, {"tool": tool.name, "ok": result.ok, "state": ToolState.CONFIRMED}
        )
        return result

    async def _record(
        self, event: ToolLifecycleEvent, tool: Tool, ctx: ToolContext, key: str
    ) -> None:
        await self._bus.publish(
            tool_lifecycle_draft(
                event,
                activity_id=ctx.activity_id,
                correlation_id=ctx.correlation_id,
                tool_name=tool.name,
                key=key,
            )
        )

    def _refuse(self, ctx: ToolContext, tool_name: str, code: str, message: str) -> ToolResult:
        """**Even when there's no result, provenance is still attached.** Errs toward the safe side
        (untrusted).
        """
        del ctx  # On failure, input trust is never carried forward (no value means nothing to join)
        log.info("tool.refused", tool=tool_name, code=code, reason=message)
        return ToolResult(
            ok=False,
            value=None,
            error=ToolError(code=code, message=message),
            provenance_class=ProvenanceClass.UNTRUSTED,
            trust_level=TrustLevel.TAINTED,
        )


def _stable_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        # Even for input that can't be digested, **the audit entry is never left empty**
        return repr(sorted(value.items()))
