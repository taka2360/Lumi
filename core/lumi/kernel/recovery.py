"""The **vocabulary and types** for Crash Recovery.

Design → docs/architecture/recovery.md

> **Phase 1 only records these. No recovery logic is written yet** (Phase 4a).

The vocabulary is fixed up front because adding it later would mean touching every
Command / Tool signature and every execution path — effectively a rewrite.

## Three-stage recording

```
INTENT_RECORDED     Passed Policy, bind/verify done too. About to execute
       ↓
EXECUTION_STARTED   Entered execute
       ↓
EXECUTION_CONFIRMED Result received and persisted
```

**Anything with `INTENT_RECORDED` but no `CONFIRMED` / `ABORTED` is "unresolved."**

With only `INTENT_RECORDED`, **it likely never executed** (safe to re-run).
With `STARTED` present, **it may have executed** (must not re-run).
Without this distinction, recovery would be too conservative to be usable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from lumi.kernel.event import DomainEventDraft, activity_stream
from lumi.kernel.ids import ActivityId, CorrelationId


class ToolLifecycleEvent(StrEnum):
    """Belongs to `stream_key = activity:<id>`, so ordering is guaranteed."""

    INTENT_RECORDED = "ToolIntentRecorded"
    EXECUTION_STARTED = "ToolExecutionStarted"
    EXECUTION_CONFIRMED = "ToolExecutionConfirmed"
    #: Explicitly aborted (no result)
    EXECUTION_ABORTED = "ToolExecutionAborted"
    #: Has intent but no confirmation. **Not a "failure." Outcome unknown**
    EXECUTION_UNKNOWN = "ToolExecutionUnknown"


def digest(value: str) -> str:
    """A digest for logs and audit records. **So the raw value is never kept.**"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def idempotency_key(
    *,
    activity_id: ActivityId,
    tool_name: str,
    security_scope_canonical: str,
    args_digest: str,
) -> str:
    """`hash(activity_id, tool_name, security_scope, args_digest)`

    **Includes the normalized target (`SecurityScope.canonical`), not the raw argument.**
    With raw arguments, `~/a/../b` and `~/b` would look like different operations,
    breaking "is this the same operation."

    The `SecurityScope` type itself is never accepted here. **The kernel knows
    nothing about permission** (dependency direction, docs/architecture/core.md §4),
    so it takes the already-normalized string instead.
    """
    joined = "\x1f".join((str(activity_id), tool_name, security_scope_canonical, args_digest))
    return digest(joined)


def tool_lifecycle_draft(
    event: ToolLifecycleEvent,
    *,
    activity_id: ActivityId,
    correlation_id: CorrelationId,
    tool_name: str,
    key: str,
    causation_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> DomainEventDraft:
    """Builds the DomainEvent for the three-stage recording.

    **Never puts raw arguments or results into the payload.** Only digests and
    identifiers go in (the same idea as `raw_input_digest` in
    docs/architecture/permission.md §7). History is hard to erase, so nothing that
    might later need erasing goes in to begin with.
    """
    payload: dict[str, Any] = {
        "tool": tool_name,
        "idempotency_key": key,
    }
    if extra:
        payload.update(extra)
    return DomainEventDraft(
        stream_key=activity_stream(activity_id),
        type=event.value,
        payload=payload,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
