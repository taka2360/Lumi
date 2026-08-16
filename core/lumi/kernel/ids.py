"""ID types the Kernel deals with.

**Wrapped as `NewType`.** Passing a plain `str` fails the type checker.
A mix-up — passing a `JobId` where an `ActivityId` is expected, for example — can't be
caught at runtime (both look like hex strings), so this closes that gap here instead.
"""

from __future__ import annotations

from typing import NewType
from uuid import uuid4

ActivityId = NewType("ActivityId", str)
JobId = NewType("JobId", str)
EventId = NewType("EventId", str)
CommandId = NewType("CommandId", str)

#: The ID tracked across a chain of processing. Shared by logs, audit, and DomainEvents.
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
