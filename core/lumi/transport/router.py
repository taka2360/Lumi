"""Stage → Core requests. **The allowlist is the registry** (ADR-028).

Boundaries → docs/contracts/security-boundaries.md B2 · Namespaces → ADR-042

A method that nobody registered does not exist. There is no default handler, no fallback
and no pattern match: **that is the fail-closed shape the boundary needs**, and it is why
adding a route is always a deliberate act rather than the absence of a check.

Two rules decide whether a request reaches a handler at all, and they are both here so a
reader can see them together:

1. **the method is registered**, and
2. **it is in the namespace of the role that sent it.** `os.*` from a Stage is refused
   without ever reaching the handler, whatever that handler would have done

## Always answering is the point

A client left waiting forever cannot tell a slow Core from a dead one, so every path out
of `serve` sends a `result` — including the ones where the handler raised, timed out, or
produced a payload that would not encode. **`internal_error` never carries the exception
text**: it is logged here in full and the client is told only that it failed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.exceptions import ConnectionClosed

from lumi import logging as lumi_logging
from lumi.transport.connections import Connection
from lumi.transport.protocol import Request, Result, method_matches_role

log = lumi_logging.get_logger(__name__)

#: How long an inbound handler may take before the request is answered `timeout` anyway.
#: `serve` promises to always answer, and a handler that blocks forever is the one way that
#: promise breaks — the client cannot tell it apart from a hung Core.
INBOUND_REQUEST_TIMEOUT_S = 10.0

#: A handler for a Stage → Core request. Returns the payload to answer with, or raises
#: `RequestRefused` to answer with an error (ADR-028).
InboundHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class RequestRefused(Exception):
    """The handler refused. **The reason travels back to the client**, never just "failed"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Router:
    """The registered inbound methods, and what happens to one request."""

    __slots__ = ("_handlers",)

    def __init__(self) -> None:
        self._handlers: dict[str, InboundHandler] = {}

    def register(self, method: str, handler: InboundHandler) -> None:
        """Registers a method the client may initiate.

        **Registering twice is an error, not the later one winning.** Two handlers for one
        method means one of them is dead code that its author believes is running.
        """
        if method in self._handlers:
            raise ValueError(f"{method} is already registered")
        self._handlers[method] = handler

    async def serve(self, connection: Connection, request: Request) -> None:
        """Answers one inbound request. **Always answers** — a client left waiting forever
        is indistinguishable from a hung Core.
        """
        handler = self._handlers.get(request.method)
        if handler is None or not method_matches_role(request.method, connection.role):
            # **fail-closed.** An unregistered method, or one outside this role's
            # namespace, is refused without ever reaching any handler
            log.warning(
                "transport.request.refused",
                role=connection.role.value,
                method=request.method,
                registered=handler is not None,
            )
            await self._answer(connection, request, ok=False, error="unknown_method")
            return

        try:
            async with asyncio.timeout(INBOUND_REQUEST_TIMEOUT_S):
                payload = await handler(request.payload)
        except RequestRefused as refused:
            log.info("transport.request.refused", method=request.method, reason=refused.reason)
            await self._answer(connection, request, ok=False, error=refused.reason)
        except TimeoutError:
            # **The client gets an answer even when the handler never came back.** Waiting
            # forever on a request is indistinguishable from Core having died
            log.warning("transport.request.timeout", method=request.method)
            await self._answer(connection, request, ok=False, error="timeout")
        except Exception:
            # **Never leaks the exception text to the client.** It is logged in full here
            log.exception("transport.request.failed", method=request.method)
            await self._answer(connection, request, ok=False, error="internal_error")
        else:
            await self._answer(connection, request, ok=True, payload=payload)

    async def _answer(
        self,
        connection: Connection,
        request: Request,
        *,
        ok: bool,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        frame = Result(corr_id=request.id, ok=ok, payload=payload or {}, error=error)
        try:
            encoded = frame.encode()
        except Exception:
            # **The client must not wait forever because Core produced an invalid payload.**
            # Keep the original exception in the log, then answer with a fixed JSON-safe error.
            log.exception(
                "transport.answer.failed", role=connection.role.value, method=request.method
            )
            encoded = Result(
                corr_id=request.id,
                ok=False,
                payload={},
                error="internal_error",
            ).encode()

        try:
            await connection.ws.send(encoded)
        except ConnectionClosed:
            # The client went away mid-request. **Not worth failing over**
            log.debug("transport.answer.closed", role=connection.role.value, method=request.method)
        except Exception:
            # A send failure is independent of encoding. **It is still logged, but never allowed
            # to mask the original handler or encoding failure.**
            log.exception(
                "transport.answer.failed", role=connection.role.value, method=request.method
            )
