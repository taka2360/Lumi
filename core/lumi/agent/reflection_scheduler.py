"""When to go and think about what was said. **Not what to think about.**

Design → docs/architecture/memory.md §4
Decision → docs/decisions/ADR-045-core-module-layering.md

Reflection has two halves and they belong to different layers. **What to extract from a
transcript is memory's question** (`memory/reflection.py`). **When Lumi may go and do it
is the agent's**, because the answer is made of the agent's own concepts: how long since
the last turn, whether the user asked to be remembered, and whether the inference lease
is free.

Putting the schedule under `memory/` would mean `memory` importing `agent`, which
core.md §4 forbids for exactly this reason — the record does not know the rememberer's
circumstances. It is also where Phase 3 puts "when may Lumi speak unprompted", and the
two should be answerable side by side rather than in different packages.

## Why a poll rather than a timer per turn

What is being waited for is the *absence* of turns, and there is no event for that.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Final, cast

from lumi import logging as lumi_logging
from lumi.agent.maintenance import MaintenanceJobs
from lumi.agent.reactive import ReactiveLoop
from lumi.kernel.arbiter import AttentionArbiter
from lumi.memory.reflection import ReflectionJob
from lumi.memory.store import MemoryStore
from lumi.providers.base import ProviderError, ProviderKind
from lumi.providers.llm.base import LLMProvider
from lumi.providers.llm.sampling import Purpose, options_for
from lumi.providers.registry import ProviderRegistry
from lumi.storage.memory import EpisodeStore
from lumi.transport.methods import METHOD_PANEL_MEMORY
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)


#: is the absence of turns, and absence does not raise an event.
#:
#: **Shorter than `REFLECTION_ASKED_IDLE_AFTER`**, or 「覚えておいて」 would wait out a
#: whole poll interval past the threshold it was given — a 20-second promise answered in
#: a minute. The check itself is one attribute read and a subtraction.
REFLECTION_CHECK_SECONDS: Final = 15.0

#: How quiet it has to be before Lumi thinks about what was said [Provisional]. Short
#: enough that a session usually gets reflected on while it is still open; long enough that
#: **a pause for breath is not mistaken for the end of a conversation.**
REFLECTION_IDLE_AFTER: Final = timedelta(seconds=120)

#: How quiet it has to be **after the user asked for something to be remembered**
#: [Provisional]. Long enough not to run inside the pause between two sentences of the
#: same thought; short enough that 「覚えておいて」 is acted on while the conversation it
#: belongs to is still the one happening (docs/architecture/memory.md §4).
REFLECTION_ASKED_IDLE_AFTER: Final = timedelta(seconds=20)


class ReflectionScheduler:
    """Waits for quiet, then runs one reflection pass."""

    __slots__ = (
        "_arbiter",
        "_asked",
        "_episodes",
        "_loop",
        "_maintenance",
        "_memories",
        "_model",
        "_providers",
        "_server",
    )

    def __init__(
        self,
        *,
        loop: ReactiveLoop | None,
        arbiter: AttentionArbiter,
        providers: ProviderRegistry,
        memories: MemoryStore,
        episodes: EpisodeStore,
        maintenance: MaintenanceJobs,
        server: WsServer,
        model: Callable[[], str],
    ) -> None:
        self._loop = loop
        self._arbiter = arbiter
        self._providers = providers
        self._memories = memories
        self._episodes = episodes
        self._maintenance = maintenance
        self._server = server
        #: **Read once per pass**, because the model can change under it: the runtime
        #: rebinds which one is loaded, and a name captured at construction would send a
        #: reflection to an engine that is no longer there.
        self._model = model
        self._asked = False

    async def run(self) -> None:
        """Extract memories from what has been said, **while nobody is talking.**

        docs/architecture/memory.md §4 lists three triggers: session end, a long idle, and
        an explicit request. **Two of them are the same mechanism with a different
        threshold**, and the third falls out of it: a session that ends leaves its
        transcript for the next start, where "no turn yet" is already an idle period.
        Holding shutdown open for an inference would trade a clean exit for a chore.

        | trigger | what it waits for |
        |---|---|
        | a long idle | `REFLECTION_IDLE_AFTER` of quiet |
        | 「覚えておいて」 | `REFLECTION_ASKED_IDLE_AFTER` of quiet — **still quiet, not now** |
        | session end | the next start's first idle period |

        **The explicit request does not skip the wait.** Reflection takes an inference
        lease, and starting one while the user is mid-sentence would put the extraction in
        contention with the reply to the very sentence that asked for it.

        The interval is a poll rather than a timer reset per turn, because what is being
        waited for is the *absence* of turns — there is no event for that.
        """
        if self._loop is None:
            return
        while True:
            await asyncio.sleep(REFLECTION_CHECK_SECONDS)
            # **Read once per pass**, because reading it clears it: a request must not be
            # dropped by a loop that checked, decided it was too soon, and moved on.
            asked = self._loop.take_remember_request()
            if asked:
                self._asked = True
            wait = REFLECTION_ASKED_IDLE_AFTER if self._asked else REFLECTION_IDLE_AFTER
            if self._loop.idle_for() < wait:
                continue
            self._asked = False
            try:
                llm = cast(LLMProvider, await self._providers.get(ProviderKind.LLM))
            except ProviderError as error:
                # **Not an error worth a stack trace.** The engine is not up yet; the next
                # idle period will try again, and nothing was lost.
                log.info("reflection.llm_unavailable", reason=error.reason)
                continue
            job = ReflectionJob(
                arbiter=self._arbiter,
                llm=llm,
                store=self._memories,
                episodes=self._episodes,
                options=options_for(self._model(), Purpose.EXTRACTION),
            )
            try:
                report = await job.run()
                if report.learned:
                    # **What was just learned should be findable.** Indexing is cheap and
                    # this is already the idle path, so it costs the user nothing.
                    await self._maintenance.index_memories()
                    # **A nudge, not the memories themselves.** An open memory window asks
                    # for what it wants to show; sending the records here would mean
                    # guessing which page it is on (ADR-042).
                    await self._server.notify(
                        Role.PANEL,
                        METHOD_PANEL_MEMORY,
                        {"written": report.written, "superseded": report.superseded},
                    )
            except Exception:
                # ★ **One bad pass must not end reflection for the session.** This coroutine
                # is spawned once and never restarted (`runtime.py`), so anything escaping
                # here would take the loop with it and Lumi would quietly stop making
                # memories until the next launch. **That is the same stopped queue the batch
                # retry exists to prevent**, reached from the outside.
                #
                # **This is the last resort, not the handler.** `ReflectionJob.run()` keeps
                # its own report when it dies partway, precisely so that writes it already
                # made still reach `index_memories()` above — a pass whose failure surfaced
                # here instead wrote nothing, because `unreflected()` is all that runs before
                # the first write. Repeating it is safe either way: the watermark only moves
                # after the writes land.
                log.exception("reflection.pass_failed")
