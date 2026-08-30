"""Who is connected, and how many of them there may be.

Boundaries → docs/contracts/security-boundaries.md B2 / B3 · Decision → ADR-042

**`panel` is the only role that may hold more than one connection** — the user can have the
settings, memory and inspector windows open at once. Every other role holds at most one, and
that is enforced here rather than assumed: a second Stage claiming the role does not join
the first, it replaces it, and the one being replaced has its pending commands failed rather
than left hanging.

## A dropped connection fails what was waiting on it

`invoke` returns a future that a `result` frame completes. When the connection goes away
those futures have nobody left to complete them, and a caller awaiting one would wait for
the process lifetime. **They are failed explicitly** — never left pending, never silently
resolved with an empty answer.
"""

from __future__ import annotations

import asyncio

from websockets.asyncio.server import ServerConnection

from lumi import logging as lumi_logging
from lumi.transport.protocol import MULTI_CONNECTION_ROLES, Result, Role

log = lumi_logging.get_logger(__name__)

#: The close code returned on authentication failure (1000-2999 is reserved, so the 4000s
#: are used).
CLOSE_UNAUTHORIZED = 4401
CLOSE_PROTOCOL_ERROR = 4400


class NotConnectedError(RuntimeError):
    """The peer isn't connected. **Raised as an exception so this never silently degrades.**"""


class Connection:
    """One authenticated connection. **At most one per role, except `panel`** (ADR-042)."""

    def __init__(self, role: Role, ws: ServerConnection) -> None:
        self.role = role
        self.ws = ws
        self._pending: dict[str, asyncio.Future[Result]] = {}

    def register(self, command_id: str) -> asyncio.Future[Result]:
        future: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        return future

    def resolve(self, result: Result) -> bool:
        future = self._pending.pop(result.corr_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(result)
        return True

    def abandon_all(self, reason: str) -> None:
        """The connection dropped. **Fails any pending commands with an error** (never leaves them
        hanging).
        """
        for command_id, future in self._pending.items():
            if not future.done():
                future.set_exception(NotConnectedError(f"{command_id}: {reason}"))
        self._pending.clear()


class ConnectionRegistry:
    """The connections held per role. **The place the one-per-role rule is applied.**"""

    __slots__ = ("_by_role",)

    def __init__(self) -> None:
        #: **A list per role, not one connection.** `panel` may hold several; every other
        #: role holds at most one, which `admit` enforces rather than assumes.
        self._by_role: dict[Role, list[Connection]] = {}

    def is_connected(self, role: Role) -> bool:
        return bool(self._by_role.get(role))

    def count(self, role: Role) -> int:
        """How many clients hold this role. **Only `panel` ever exceeds 1.**"""
        return len(self._by_role.get(role, ()))

    def peers(self, role: Role) -> list[Connection]:
        """Everything holding the role, as a copy. **Possibly none** — a notification to
        nobody is not a failure.
        """
        return list(self._by_role.get(role, ()))

    def sole(self, role: Role) -> Connection | None:
        """The one connection for a single-connection role.

        **Refuses a multi-connection role outright** rather than picking one of them: a
        caller that wanted an addressee has asked the wrong question.
        """
        if role in MULTI_CONNECTION_ROLES:
            raise ValueError(f"{role.value} may hold several connections; it has no sole peer")
        existing = self._by_role.get(role)
        return existing[0] if existing else None

    async def admit(self, connection: Connection) -> int:
        """Registers it and returns how many now hold the role.

        **A reconnect replaces rather than joins.** The one being replaced is discarded
        here and not merely forgotten: closing it is what stops a ghost connection living
        on Core's side, and abandoning its pending commands is what stops whoever was
        waiting on them from waiting for the rest of the session.
        """
        peers = self._by_role.setdefault(connection.role, [])
        if connection.role not in MULTI_CONNECTION_ROLES:
            for previous in list(peers):
                log.info("transport.reconnect", role=connection.role.value)
                previous.abandon_all("Replaced by reconnection")
                await previous.ws.close(code=1000, reason="replaced by a new connection")
            peers.clear()
        peers.append(connection)
        return len(peers)

    def release(self, connection: Connection) -> int | None:
        """Removes it and returns how many are left, or **`None` if it was already gone.**

        Already gone means it was replaced by a reconnect, which has run its own
        registration and its own hook. Reporting a disconnect for it too would tell the rest
        of Core the role went away at the moment it came back.
        """
        remaining = self._by_role.get(connection.role)
        if remaining is None or connection not in remaining:
            return None
        remaining.remove(connection)
        connection.abandon_all("Connection closed")
        return len(remaining)
