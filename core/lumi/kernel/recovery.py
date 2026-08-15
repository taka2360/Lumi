"""Crash Recovery の**語彙と型**。

設計 → docs/architecture/recovery.md

> **Phase 1 では記録するだけ。復旧ロジックは書かない**（Phase 4a）。

語彙を先に固定するのは、後から入れると全 Command / Tool のシグネチャと
全実行経路に手を入れることになり、事実上の書き直しになるため。

## 3段記録

```
INTENT_RECORDED     Policy を通過し、bind/verify も済んだ。これから execute する
       ↓
EXECUTION_STARTED   execute に入った
       ↓
EXECUTION_CONFIRMED 結果を受け取り、永続化した
```

**`INTENT_RECORDED` があって `CONFIRMED` / `ABORTED` が無いものが「未確定」。**

`INTENT_RECORDED` だけなら**実行されていない可能性が高い**（再実行してよい）。
`STARTED` があるなら**実行された可能性がある**（再実行してはいけない）。
この区別が無いと、復旧が保守的すぎて使えなくなる。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from lumi.kernel.event import DomainEventDraft, activity_stream
from lumi.kernel.ids import ActivityId, CorrelationId


class ToolLifecycleEvent(StrEnum):
    """`stream_key = activity:<id>` に属するので順序が保証される。"""

    INTENT_RECORDED = "ToolIntentRecorded"
    EXECUTION_STARTED = "ToolExecutionStarted"
    EXECUTION_CONFIRMED = "ToolExecutionConfirmed"
    #: 明示的に中断した（結果は無い）
    EXECUTION_ABORTED = "ToolExecutionAborted"
    #: intent あり confirm なし。**「失敗」ではない。結果が不明**
    EXECUTION_UNKNOWN = "ToolExecutionUnknown"


def digest(value: str) -> str:
    """ログと監査に残す用のダイジェスト。**生の値を残さない**ため。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def idempotency_key(
    *,
    activity_id: ActivityId,
    tool_name: str,
    security_scope_canonical: str,
    args_digest: str,
) -> str:
    """`hash(activity_id, tool_name, security_scope, args_digest)`

    **生の引数ではなく、正規化後の対象（`SecurityScope.canonical`）を含める。**
    生の引数だと `~/a/../b` と `~/b` が別の操作に見えてしまい、
    「同じ操作か」の判定が壊れる。

    `SecurityScope` 型そのものは受け取らない。**kernel は permission を知らない**
    （依存の向き。docs/architecture/core.md §4）ので、正規化済みの文字列を受け取る。
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
    """3段記録の DomainEvent を作る。

    **payload に生の引数も結果も入れない。** 入れるのはダイジェストと識別子だけ
    （docs/architecture/permission.md §7 の `raw_input_digest` と同じ考え方）。
    履歴は消しにくいので、消したくなるものを最初から入れない。
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
