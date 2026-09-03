"""When Lumi goes and thinks about what was said → docs/architecture/memory.md §4.

**What is worth testing here is not the schedule, it is the survival.** The scheduler is
spawned once and never restarted (`agent/runtime.py`), so its loop is the only thing
standing between one bad pass and a session that quietly stops making memories — the same
stopped queue `memory/reflection.py` shrinks its batches to avoid, reached from outside.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from lumi.agent import reflection_scheduler as module
from lumi.agent.reflection_scheduler import ReflectionScheduler


class NeverBusy:
    """A `ReactiveLoop` that has been quiet for an hour and was never asked to remember."""

    def take_remember_request(self) -> bool:
        return False

    def idle_for(self) -> timedelta:
        return timedelta(hours=1)


class BrokenEpisodes:
    """The store the pass reads first. **Raises once**, the way a locked database would."""

    def __init__(self) -> None:
        self.calls = 0
        self.tried_again = asyncio.Event()

    async def unreflected(self, limit: int) -> list[tuple[str, int]]:
        del limit
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("database is locked")
        self.tried_again.set()
        return []


class Providers:
    async def get(self, kind: Any) -> Any:
        del kind
        return object()


async def test_a_pass_that_raises_does_not_end_reflection_for_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ The scheduler is started once. **An exception escaping its loop is permanent** —
    Lumi keeps talking and never remembers anything again, and nothing on screen says so.

    Repeating is safe by construction: the watermark only moves after a pass's writes
    land, so a pass that died read nothing it did not read again.
    """
    monkeypatch.setattr(module, "REFLECTION_CHECK_SECONDS", 0.0)
    episodes = BrokenEpisodes()
    scheduler = ReflectionScheduler(
        loop=NeverBusy(),  # type: ignore[arg-type]
        arbiter=object(),  # type: ignore[arg-type]
        providers=Providers(),  # type: ignore[arg-type]
        memories=object(),  # type: ignore[arg-type]
        episodes=episodes,  # type: ignore[arg-type]
        maintenance=object(),  # type: ignore[arg-type]
        server=object(),  # type: ignore[arg-type]
        model=lambda: "fake",
    )

    task = asyncio.create_task(scheduler.run())
    try:
        await asyncio.wait_for(episodes.tried_again.wait(), timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert episodes.calls >= 2
