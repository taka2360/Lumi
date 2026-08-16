"""Crash Recovery's vocabulary and `idempotency_key`.

**docs/architecture/recovery.md tests 1 / 7.** Recovery logic itself is Phase 4a.
"""

from __future__ import annotations

from lumi.kernel.ids import new_activity_id, new_correlation_id
from lumi.kernel.recovery import (
    ToolLifecycleEvent,
    digest,
    idempotency_key,
    tool_lifecycle_draft,
)


def test_idempotency_key_includes_the_security_scope() -> None:
    """**Includes the normalized target, not the raw argument.**

    `~/a/../b` and `~/b` can be judged as the same operation only because the key
    includes the normalized `SecurityScope.canonical`.
    """
    activity = new_activity_id()
    args = digest("{}")
    one = idempotency_key(
        activity_id=activity,
        tool_name="fs.write",
        security_scope_canonical="C:\\a\\b.txt",
        args_digest=args,
    )
    other = idempotency_key(
        activity_id=activity,
        tool_name="fs.write",
        security_scope_canonical="C:\\a\\c.txt",
        args_digest=args,
    )
    assert one != other


def test_idempotency_key_is_stable() -> None:
    activity = new_activity_id()
    kwargs = {
        "activity_id": activity,
        "tool_name": "fs.write",
        "security_scope_canonical": "C:\\a\\b.txt",
        "args_digest": digest("{}"),
    }
    assert idempotency_key(**kwargs) == idempotency_key(**kwargs)  # type: ignore[arg-type]


def test_lifecycle_events_belong_to_the_activity_stream() -> None:
    """Belongs to `activity:<id>`, so **ordering is guaranteed.**"""
    activity = new_activity_id()
    draft = tool_lifecycle_draft(
        ToolLifecycleEvent.INTENT_RECORDED,
        activity_id=activity,
        correlation_id=new_correlation_id(),
        tool_name="fs.write",
        key="k",
    )
    assert draft.stream_key == f"activity:{activity}"
    assert draft.type == "ToolIntentRecorded"


def test_lifecycle_payload_carries_no_raw_arguments() -> None:
    """**History is hard to erase.** Nothing that might need erasing goes in to begin with."""
    draft = tool_lifecycle_draft(
        ToolLifecycleEvent.EXECUTION_STARTED,
        activity_id=new_activity_id(),
        correlation_id=new_correlation_id(),
        tool_name="fs.write",
        key="k",
    )
    assert set(draft.payload) == {"tool", "idempotency_key"}


def test_the_three_stage_vocabulary_is_distinct() -> None:
    """With only `INTENT_RECORDED`, it never executed; with `STARTED` present, it **may have** executed.

    Without this distinction, recovery would be too conservative to be usable.
    """
    values = {event.value for event in ToolLifecycleEvent}
    assert values == {
        "ToolIntentRecorded",
        "ToolExecutionStarted",
        "ToolExecutionConfirmed",
        "ToolExecutionAborted",
        "ToolExecutionUnknown",
    }
