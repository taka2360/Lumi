"""Tool の型。

型の定義 → docs/interfaces/tool.md / 契約 → docs/contracts/tool-execution.md

> **Tool が実装するのは `bind` と `execute` の2つだけ。** 正規化も権限判断も検証もしない。

## `execute` が `ToolResult` を返さない理由〔Phase 1 実装時に確定〕

docs は「**provenance は Core が付与する。Tool の自己申告を信じない**」と定めている
（docs/interfaces/tool.md）。それを型で保証するため、Tool が返すのは
**provenance を持たない `ToolOutcome`** とし、`ToolResult` は Tool Registry が組み立てる。

`ToolResult` を Tool に作らせると、`provenance_class=TRUSTED` と書ける経路が残る。
それは Invariant 7（No Laundering）の穴そのものである。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from lumi.kernel.activity import Actor
from lumi.kernel.cancellation import CancelToken
from lumi.kernel.ids import ActivityId, CorrelationId
from lumi.permission.policy import PermissionSpec
from lumi.permission.scope import Handle, ScopeLane
from lumi.provenance import ProvenanceClass, TrustLevel


class ToolKind(StrEnum):
    READ = "read"
    WRITE = "write"
    CONTROL = "control"
    WORKFLOW = "workflow"


@dataclass(frozen=True, slots=True)
class ToolContext:
    """`cancel_token` の扱いは `permission.cancellation` で決まる。"""

    cancel_token: CancelToken
    actor: Actor
    activity_id: ActivityId
    correlation_id: CorrelationId
    #: 呼び出し元 context の `effective_trust`
    input_trust_level: TrustLevel
    deadline: datetime | None = None
    #: 副作用を持つなら必須（docs/architecture/recovery.md §3）
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """**Tool が返せるのはここまで。** provenance を持たない。"""

    ok: bool
    value: Any | None = None
    error: ToolError | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """**Tool Registry だけが組み立てる。** provenance は Core が付与する。"""

    ok: bool
    value: Any | None
    error: ToolError | None
    provenance_class: ProvenanceClass
    trust_level: TrustLevel


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """LLM に見せるメタデータ。**`deferred=True` のものは既定で露出しない。**"""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    kind: ToolKind
    deferred: bool = False


class Tool(Protocol):
    """Class A（in-core）の Tool。

    **実装してはいけないもの**: `authorize()` / `canonicalize()` / `verify()` /
    `PermissionKernel` の呼び出し。いずれも静的検査で縛る。
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]

    lane: ScopeLane
    kind: ToolKind
    permission: PermissionSpec

    concurrency_safe: bool
    idempotent: bool
    #: 既定で LLM に露出しない（ツール数の爆発対策）
    deferred: bool

    def bind(self, ctx: ToolContext, scope: Any) -> Handle: ...

    async def execute(self, ctx: ToolContext, handle: Handle) -> ToolOutcome: ...


@dataclass(frozen=True, slots=True)
class RemoteToolDescriptor:
    """Class B。**Core は実行しない。** Extension Host に委譲する。

    〔Phase 4b で使う。ここに型だけ置いてあるのは、`register_remote` の
    fail-closed 検証条件が Class A 側と対になっているため。**実装は Phase 4b。**〕
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    lane: ScopeLane
    kind: ToolKind
    permission: PermissionSpec
    extension_id: str
    concurrency_safe: bool = False
    idempotent: bool = False
    deferred: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
