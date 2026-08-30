"""Asking the user, and what "no answer" means. **Nobody there is not a "no".**

Design → docs/architecture/setup.md §3
Decision → docs/decisions/ADR-034-gate-startup-on-complete-setup.md

Every setup question is the same shape: put the question on screen, wait a human amount
of time, take the flag down again whatever happened. Written out per question, the part
that kept being got right by copying was the `finally` — a question left marked as open
leaves the boot phase stuck on `setup`, and the Stage shows a consent screen over a Lumi
that is running.

**Declined and unanswered are different**, and this is where they stop looking alike. A
closed window and an expired wait both arrive as an exception; both mean nobody decided
anything, so `ask` returns `None` and the caller is left to say what happens next — for
some questions that is "ask again next start", for others "do nothing at all".

## Why a Protocol

Setup's judgement — what is missing, what to ask, what an answer means — is worth testing
without a WebSocket and without a Stage. It was not testable that way while the questions
were `server.invoke` calls buried in the decisions, which is also why the three
`except (NotConnectedError, TimeoutError)` clauses had drifted into doing three different
things about the same event.
"""

from __future__ import annotations

from typing import Protocol

from lumi.setup.broadcast import SetupStateBroadcaster
from lumi.transport.methods import METHOD_SETUP_PROMPT
from lumi.transport.protocol import Result, Role
from lumi.transport.server import NotConnectedError, WsServer

#: How long to wait for the user's choice. **Human time**, so it's long.
PROMPT_TIMEOUT_S = 600.0


class Prompter(Protocol):
    """Puts one question to the user and waits for the answer."""

    async def ask(self, payload: dict[str, object]) -> Result | None:
        """The answer, or **`None` when nobody answered.**"""
        ...


class WsPrompter:
    """Asks the Stage. **The only part of setup that needs a connection.**"""

    __slots__ = ("_server", "_state")

    def __init__(self, server: WsServer, state: SetupStateBroadcaster) -> None:
        self._server = server
        self._state = state

    async def ask(self, payload: dict[str, object]) -> Result | None:
        """Marks a question as open, asks it, and **always marks it closed again.**

        The flag is what the boot phase reads (ADR-034), so the `finally` is not tidiness:
        without it a Stage that disconnects mid-question leaves the phase on `setup`
        forever, and the next thing the user sees is a consent screen in front of a Lumi
        that already started.
        """
        self._state.asking(True)
        await self._state.publish()
        try:
            return await self._server.invoke(
                Role.STAGE, METHOD_SETUP_PROMPT, payload, timeout=PROMPT_TIMEOUT_S
            )
        except (NotConnectedError, TimeoutError):
            return None
        finally:
            self._state.asking(False)
