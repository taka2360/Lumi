"""Tool types.

Type definitions → docs/interfaces/tool.md / Contract → docs/contracts/tool-execution.md

> **A Tool implements only `bind` and `execute`.** It does no normalization, no
> permission decisions, no verification.

## Why `execute` doesn't return a `ToolResult` [finalized during Phase 1 implementation]

The docs establish that "**provenance is attached by Core. A Tool's self-reported
claim is never trusted**" (docs/interfaces/tool.md). To enforce that at the type
level, what a Tool returns is **`ToolOutcome`, which carries no provenance**, and
`ToolResult` is assembled by the Tool Registry.

Letting a Tool construct a `ToolResult` would leave a path open to write
`provenance_class=TRUSTED`. That is exactly the hole Invariant 7 (No Laundering)
exists to close.
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
    """How `cancel_token` behaves is determined by `permission.cancellation`."""

    cancel_token: CancelToken
    actor: Actor
    activity_id: ActivityId
    correlation_id: CorrelationId
    #: The caller context's `effective_trust`
    input_trust_level: TrustLevel
    deadline: datetime | None = None
    #: Required if it has a side effect (docs/architecture/recovery.md §3)
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """**This is as far as a Tool can return.** Carries no provenance."""

    ok: bool
    value: Any | None = None
    error: ToolError | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """**Only the Tool Registry assembles this.** Provenance is attached by Core."""

    ok: bool
    value: Any | None
    error: ToolError | None
    provenance_class: ProvenanceClass
    trust_level: TrustLevel


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Metadata shown to the LLM. **`deferred=True` ones aren't exposed by default.**"""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    kind: ToolKind
    deferred: bool = False


class Tool(Protocol):
    """A Class A (in-core) Tool.

    **Must never implement**: `authorize()` / `canonicalize()` / `verify()` / calling
    `PermissionKernel`. All of these are enforced by static checks.
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
    #: Not exposed to the LLM by default (guards against tool-count explosion)
    deferred: bool

    def bind(self, ctx: ToolContext, scope: Any) -> Handle: ...

    async def execute(self, ctx: ToolContext, handle: Handle) -> ToolOutcome: ...


@dataclass(frozen=True, slots=True)
class RemoteToolDescriptor:
    """Class B. **Core never executes it.** Delegated to the Extension Host.

    [Used starting Phase 4b. Only the type lives here for now, because
    `register_remote`'s fail-closed verification conditions are paired with the
    Class A side. **The implementation itself is Phase 4b.**]
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
