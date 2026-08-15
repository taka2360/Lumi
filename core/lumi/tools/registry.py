"""Tool Registry — **Kernel 実行契約を強制する唯一の場所。**

契約 → docs/contracts/tool-execution.md

```
canonicalize → decide → bind → verify → execute
```

> **契約: Policy が検査した対象と、execute が操作した対象は同一でなければならない。**

## fail-closed

登録時に条件を満たさない Tool は**起動時に例外**。実行時に毎回判定するより、
登録時に落とす方が fail-closed として強い（docs/architecture/permission.md §3.1）。

## 3段記録

`INTENT_RECORDED`（bind/verify も済んだ）→ `EXECUTION_STARTED` → `EXECUTION_CONFIRMED`。
**Phase 1 では記録するだけ。復旧は Phase 4a**（docs/architecture/recovery.md）。
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
    """**Activity の状態とは独立に遷移する**（docs/contracts/state-machines.md）。

    遷移を実行してよいのは Tool Registry のみ。Tool 自身は状態を持たない。
    """

    AUTHORIZED = "authorized"
    DENIED = "denied"
    BOUND = "bound"
    BIND_FAILED = "bind_failed"
    EXECUTING = "executing"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    #: **「失敗」ではない。** intent はあるが confirm が無い（結果が不明）
    UNKNOWN = "unknown"


class ToolRegistrationError(RuntimeError):
    """登録時の fail-closed 検証に落ちた。**起動を止める。**"""


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

    # ── 登録 ──────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        """Class A（in-core）の Tool。**条件を満たさなければ例外。**"""
        if not tool.name or not tool.description:
            raise ToolRegistrationError("メタデータが欠けている")
        if tool.name in self._tools:
            raise ToolRegistrationError(f"{tool.name}: 名前が重複している")

        if LANE_CLASS[tool.lane] is not ToolClass.A:
            # Class B の lane は out-of-process が提供する。in-core が名乗ってはならない
            # （Handle 契約が成立するのは in-core だけ → ADR-017）。
            raise ToolRegistrationError(f"{tool.name}: {tool.lane} は Class B の lane")

        if tool.permission.risk is Risk.DENIED:
            # DENIED は「実効リスク」としてのみ現れる値であって、宣言できるものではない。
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
            # キャンセルできない副作用を、ユーザー確認なしに起こさせない。
            raise ToolRegistrationError(
                f"{tool.name}: non_cancellable かつ副作用ありなら risk >= L3 が必要"
            )

        self._tools[tool.name] = tool

    def list_exposed(self) -> list[ToolDescriptor]:
        """`deferred=False` のものだけ。**ツールが50を超えても常時十数個に保つ。**"""
        return [self._describe(tool) for tool in self._tools.values() if not tool.deferred]

    def search(self, query: str) -> list[ToolDescriptor]:
        """`tool_search` メタツールの実装。**`deferred` も含む。**"""
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

    # ── 実行 ──────────────────────────────────────────────

    async def invoke(
        self, tool_name: str, ctx: ToolContext, raw_input: Mapping[str, Any]
    ) -> ToolResult:
        """**この経路以外から `Tool.execute` を呼ばない**（Invariant 2）。"""
        tool = self._tools.get(tool_name)
        if tool is None:
            return self._refuse(ctx, tool_name, "unknown_tool", "登録されていない")

        raw_digest = digest(_stable_json(raw_input))

        # 1. 正規化 — Kernel 所有。**失敗は deny**（fail-closed）
        try:
            scope = self._canonicalizers[tool.lane].canonicalize(raw_input)
        except CanonicalizationError as error:
            return self._refuse(ctx, tool_name, "canonicalization_failed", str(error))

        # 2. 権限判断 — Kernel 所有。Tool は関与しない
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
            # Phase 1 に権限プロンプト UI は無い。**`ask` は実行しないことを意味する**
            # （勝手に実行しない）。UI と Grant の発行は Phase 4a。
            return self._refuse(ctx, tool_name, auth.decision.value, auth.rule_id)

        # 3. before_tool Hook（**veto 可**）
        veto = await self._hooks.run(
            HookName.BEFORE_TOOL,
            {"tool": tool.name, "capability": tool.permission.capability, "scope": scope.canonical},
        )
        if isinstance(veto, Veto):
            return self._refuse(ctx, tool_name, "vetoed", veto.reason)

        # 4. bind — Tool が実装（ドメイン知識が必要）
        try:
            handle = tool.bind(ctx, scope)
        except Exception as error:
            log.warning("tool.bind_failed", tool=tool.name, error=str(error))
            return self._refuse(ctx, tool_name, ToolState.BIND_FAILED.value, str(error))

        # 5. verify — Kernel 所有 ★ 契約の要
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

        # **provenance は Core が付ける。** Tool の自己申告を信じない
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
        """**結果が無いときも provenance は付く。** 安全側（untrusted）に倒す。"""
        del ctx  # 失敗時は入力の trust を引き継がない（値が無いので join する対象も無い）
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
        # ダイジェストが取れない入力でも、**監査を空にしない**
        return repr(sorted(value.items()))
