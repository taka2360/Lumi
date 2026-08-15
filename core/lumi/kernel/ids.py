"""Kernel が扱う ID の型。

**`NewType` にしてある。** `str` をそのまま渡すと型検査で落ちる。
`ActivityId` を期待する場所に `JobId` を渡す、といった取り違えは
実行時には気づけない（どちらも hex 文字列に見える）ため、ここで塞ぐ。
"""

from __future__ import annotations

from typing import NewType
from uuid import uuid4

ActivityId = NewType("ActivityId", str)
JobId = NewType("JobId", str)
EventId = NewType("EventId", str)
CommandId = NewType("CommandId", str)

#: 一連の処理を跨いで追跡する ID。ログ・監査・DomainEvent が共有する。
CorrelationId = NewType("CorrelationId", str)


def _new() -> str:
    return uuid4().hex


def new_activity_id() -> ActivityId:
    return ActivityId(_new())


def new_job_id() -> JobId:
    return JobId(_new())


def new_event_id() -> EventId:
    return EventId(_new())


def new_command_id() -> CommandId:
    return CommandId(_new())


def new_correlation_id() -> CorrelationId:
    return CorrelationId(_new())
