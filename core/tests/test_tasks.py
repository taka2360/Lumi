"""The two things `spawn` promises: **the task is held, and its death is heard.**

Nothing awaits the tasks Core starts — the reactive loop, one conversation turn, a
warm-up. That is deliberate (see `lumi/tasks.py`), and it costs two guarantees that
`asyncio.create_task` does not give:

- the loop keeps only a **weak** reference, so a task nobody stored can be collected
  before it finishes
- an exception nobody retrieves surfaces **at GC time, if ever** — which is how a
  missing `arbiter.start()` left Lumi deaf for a day (2026-08-17)

Every module now starts tasks through here, so these two are worth pinning directly
rather than only through whoever happens to call it.
"""

from __future__ import annotations

import asyncio
import gc

import pytest
from structlog.testing import capture_logs

from lumi.tasks import spawn


async def test_the_task_is_reachable_without_the_caller_holding_it() -> None:
    """**A strong reference exists even when the caller keeps nothing.**

    `create_task`'s return value is the only strong reference the caller gets; drop it
    and the loop's weak one is all that is left.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    keep: set[asyncio.Task[None]] = set()

    async def work() -> None:
        started.set()
        await release.wait()

    spawn(work(), name="held", event="test.crashed", keep=keep)
    await started.wait()

    gc.collect()
    assert len(keep) == 1, "the task was not held"

    release.set()
    await asyncio.gather(*keep)
    assert keep == set(), "the task was not released once it finished"


async def test_a_crash_is_reported() -> None:
    """**The whole point.** A background failure that logs nothing is a failure nobody
    can act on, and the ones that matter here are silent by construction.
    """

    async def work() -> None:
        raise RuntimeError("boom")

    with capture_logs() as logs:
        task = spawn(work(), name="doomed", event="test.crashed")
        with pytest.raises(RuntimeError):
            await task
        # The done callback runs on the next loop pass, not on the await.
        await asyncio.sleep(0)

    crashes = [entry for entry in logs if entry["event"] == "test.crashed"]
    assert len(crashes) == 1, logs
    assert crashes[0]["log_level"] == "error"
    assert "boom" in crashes[0]["error"]


async def test_cancellation_is_not_reported() -> None:
    """**Shutdown cancels every one of these.** Reporting that would bury the exits that
    matter under the ones that do not.
    """

    async def work() -> None:
        await asyncio.Event().wait()

    with capture_logs() as logs:
        task = spawn(work(), name="cancelled", event="test.crashed")
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    assert [entry for entry in logs if entry["event"] == "test.crashed"] == []


async def test_a_normal_return_is_silent_unless_returning_is_news() -> None:
    """Most of these run until shutdown, so returning is unremarkable — except where it
    is not. The reactive loop returns exactly when there is no input device.
    """

    async def work() -> None:
        return None

    with capture_logs() as quiet:
        await spawn(work(), name="quiet", event="test.crashed")
        await asyncio.sleep(0)
    assert [entry for entry in quiet if entry["event"] == "test.stopped"] == []

    with capture_logs() as noisy:
        await spawn(work(), name="loud", event="test.crashed", on_return="test.stopped")
        await asyncio.sleep(0)
    stops = [entry for entry in noisy if entry["event"] == "test.stopped"]
    assert len(stops) == 1
    assert stops[0]["log_level"] == "info"
