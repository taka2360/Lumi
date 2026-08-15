"""Crash Recovery の語彙と `idempotency_key`。

**docs/architecture/recovery.md テスト 1 / 7。** 復旧ロジックは Phase 4a。
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
    """**生の引数ではなく正規化後の対象を含める。**

    `~/a/../b` と `~/b` が同じ操作だと判定できるのは、
    正規化後の `SecurityScope.canonical` を鍵に含めているからである。
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
    """`activity:<id>` に属するので**順序が保証される。**"""
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
    """**履歴は消しにくい。** 消したくなるものを最初から入れない。"""
    draft = tool_lifecycle_draft(
        ToolLifecycleEvent.EXECUTION_STARTED,
        activity_id=new_activity_id(),
        correlation_id=new_correlation_id(),
        tool_name="fs.write",
        key="k",
    )
    assert set(draft.payload) == {"tool", "idempotency_key"}


def test_the_three_stage_vocabulary_is_distinct() -> None:
    """`INTENT_RECORDED` だけなら未実行、`STARTED` があれば実行された**かもしれない**。

    この区別が無いと、復旧が保守的すぎて使えなくなる。
    """
    values = {event.value for event in ToolLifecycleEvent}
    assert values == {
        "ToolIntentRecorded",
        "ToolExecutionStarted",
        "ToolExecutionConfirmed",
        "ToolExecutionAborted",
        "ToolExecutionUnknown",
    }
