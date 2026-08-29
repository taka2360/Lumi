"""Starting background tasks, and reporting how they ended.
**A task that dies has to say so.**

Nothing awaits the tasks Core spawns to run in the background — the warmup, the
reactive loop, one conversation turn. That is deliberate: awaiting the reactive loop
would make it impossible to notice a `SPEECH_STARTED` arriving mid-utterance, which is
the same as having no barge-in at all.

**The cost is that asyncio only surfaces an unretrieved exception at GC time**, whenever
that happens to be. A missing `arbiter.start()` stayed invisible that way for a day
(2026-08-17): the reactive loop died on the first VAD event, Lumi went deaf for the rest
of the session, and nothing anywhere said so. A dead warmup is quieter still — a cold
engine that only shows up as a slow first reply, or a boot phase nobody will ever move.

So every such task gets a done callback from here. **Loud, and still listening.**
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)


def report_task_exit(
    event: str, *, on_return: str | None = None
) -> Callable[[asyncio.Task[Any]], None]:
    """Builds a done callback that logs why a task ended.

    `event` is logged at error level when the task raised. `on_return` is logged at info
    level when it returned normally — pass it only when returning is itself worth
    knowing (the reactive loop returns on its own exactly when there is no input device,
    and that is news). Omitting it means a normal return is unremarkable and silent.

    **Cancellation is never reported.** It is how normal shutdown ends every one of
    these tasks, and logging it would bury the exits that matter under the ones that don't.
    """

    def report(task: asyncio.Task[Any]) -> None:
        # **`cancelled()` is checked before `exception()`, and the order is not
        # negotiable**: `exception()` on a cancelled task raises `CancelledError`, which
        # here would kill the callback that exists to make failures visible — the
        # reporter would become the thing that fails silently.
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error(event, error=str(error), exc_info=error)
        elif on_return is not None:
            log.info(on_return)

    return report


def spawn[T](
    coroutine: Coroutine[Any, Any, T],
    *,
    name: str,
    event: str,
    keep: set[asyncio.Task[T]] | None = None,
    on_return: str | None = None,
) -> asyncio.Task[T]:
    """Start a background task that is **kept and heard from.**

    Two things go wrong with a bare `asyncio.create_task`, and they go wrong quietly.
    The loop holds only a weak reference, so a task nobody stored can be collected
    mid-flight; and an exception nobody retrieves surfaces at GC time, if ever.

    `keep` is for callers that run several at once. A caller that holds exactly one
    stores the returned task itself — either way there is a strong reference, which is
    what `lumi.tasks` exists to make unavoidable.

    **This is not a place to await.** Everything here stays fire-and-forget on purpose
    (see the module docstring); what it adds is a way to find out when one dies.
    """
    task = asyncio.create_task(coroutine, name=name)
    if keep is not None:
        keep.add(task)
        task.add_done_callback(keep.discard)
    task.add_done_callback(report_task_exit(event, on_return=on_return))
    return task
