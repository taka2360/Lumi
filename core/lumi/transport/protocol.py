"""The WS protocol — **pure types and validation only.** Testable without opening a socket.

Confirmation level: **Provisional** (docs/DESIGN.md §8 "The WS protocol's concrete schema").

## Namespaces and their paths (docs/architecture/core.md §3)

| Namespace | Path | Direction |
|---|---|---|
| `os.*`    | Core → Shell | Requests for OS-privileged operations |
| `stage.*` | Core → Stage | Lumi's expression and state |

> **`stage.*` must never request OS privileges.**

This is enforced at the type level. A send whose `method` namespace doesn't match the
connection's role is rejected **before it's sent** (`method_matches_role`). No path
exists for `os.*` to reach the Stage.

## Tokens are separated per role

Shell and Stage each hold a **different token.** Sharing one would let a compromised
Stage (B2: never trust the Stage) connect as `role: "shell"` and **hijack `os.*` commands.**
Shell generates both and only hands the Stage-specific one to the Stage.

## In Phase 0, only Core issues commands

A `command` from a client (Shell / Stage) to Core is **never accepted.**
The only things a client may send are `hello` and a `result` in response to a Core command.

A user's choice (e.g. "fetch or not" during first-run setup) comes back as **a
`result` in response to a command Core sent.** Core asks, the user answers. Keeping
this direction fixed structurally eliminates any path for "the Stage makes Core do
something" (B2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import uuid4

PROTOCOL_VERSION: Final = 1

# : Time allowed to wait for hello. Closed once exceeded (never holds an unauthenticated connection
# open indefinitely).
HELLO_TIMEOUT_S: Final = 5.0

# : The WS ping interval and how long to wait for a response. The mechanism for detecting that one
# side has died.
PING_INTERVAL_S: Final = 5.0
PING_TIMEOUT_S: Final = 10.0


class Role(StrEnum):
    """The kind of client connecting."""

    SHELL = "shell"
    STAGE = "stage"
    #: The auxiliary windows — settings, inspector, memory (ADR-042). **Several of them
    #: can be open at once**, which is why it is a role of its own rather than more
    #: `stage` connections: the character's connection is never taken from it.
    PANEL = "panel"


#: The namespace Core is allowed to send for each role. **Re-read B2/B3 before adding a line here.**
NAMESPACE_BY_ROLE: Final[dict[Role, str]] = {
    Role.SHELL: "os.",
    Role.STAGE: "stage.",
    Role.PANEL: "panel.",
}

#: Roles that may hold **more than one connection at a time** (ADR-042).
#:
#: **A role in here cannot be the target of `invoke`.** Waiting for an answer needs one
#: addressee, and "whichever panel replied first" is not an answer — it is a race. Core
#: only ever notifies panels, and a notification with nowhere to go is not a failure.
MULTI_CONNECTION_ROLES: Final[frozenset[Role]] = frozenset({Role.PANEL})


class ProtocolError(ValueError):
    """A protocol violation. **Never swallowed.** The connection is closed and it's logged."""


def method_matches_role(method: str, role: Role) -> bool:
    """Whether `method`'s namespace matches the role."""
    return method.startswith(NAMESPACE_BY_ROLE[role])


def new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class Hello:
    """Client → Core. The first message of a connection."""

    role: Role
    token: str


@dataclass(frozen=True, slots=True)
class Command:
    """Core → client. **Something that needs a result.**"""

    id: str
    method: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "command",
                "id": self.id,
                "method": self.method,
                "payload": self.payload,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class Notify:
    """Core → client. **Something that doesn't need a result.**

    Distinguished from a command by "does it need a result?" (the criterion from
    docs/contracts/event-model.md). Progress notifications and state broadcasts go
    here. Since no response is awaited, Core never stalls even if the peer is slow.
    """

    method: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "notify",
                "method": self.method,
                "payload": self.payload,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class Result:
    """Client → Core. A response to a command.

    When `ok=False`, `error` holds the reason. **Never silently swallowed.**
    """

    corr_id: str
    ok: bool
    payload: dict[str, Any]
    error: str | None = None

    def encode(self) -> str:
        """Core → client, answering a `Request` (ADR-028).

        The same frame the client sends back for a `Command`. **Deliberately one shape** —
        a caller waiting for an answer should not have to care which direction started it.
        """
        return json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "result",
                "corr_id": self.corr_id,
                "ok": self.ok,
                "payload": self.payload,
                "error": self.error,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class Request:
    """A request **from** a client. **The Stage asks; Core decides** (ADR-028).

    Deliberately not called `Command`. Core → Stage is a `Command` (Core decided, the
    Stage obeys); Stage → Core is a `Request` (the Stage asked, Core decides).
    **Sharing one name would make the direction unreadable in code and in logs**, and the
    asymmetry is the whole point of the boundary.
    """

    id: str
    method: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Welcome:
    """Core → client. Signals that authentication succeeded."""

    protocol_version: int = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps({"v": self.protocol_version, "kind": "welcome"}, ensure_ascii=False)


def _require_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Cannot parse JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProtocolError("Top-level message must be an object")
    return parsed


def parse_hello(raw: str) -> Hello:
    """Parses the first message right after connecting. **Raises on failure** (fail-closed)."""
    message = _require_object(raw)
    if message.get("kind") != "hello":
        raise ProtocolError("First message must be hello")
    if message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError(f"Protocol version mismatch: {message.get('v')!r}")

    raw_role = message.get("role")
    if not isinstance(raw_role, str):
        raise ProtocolError("Missing role")
    try:
        role = Role(raw_role)
    except ValueError as exc:
        raise ProtocolError(f"Unknown role: {raw_role!r}") from exc

    token = message.get("token")
    if not isinstance(token, str) or not token:
        raise ProtocolError("Missing token")

    return Hello(role=role, token=token)


def parse_client_message(raw: str) -> Result | Request:
    """Parses a message arriving from the client after authentication.

    Two kinds are accepted: `result` (an answer to Core's `Command`) and **`request`**
    (the client asking something → ADR-028).

    **A `command` from the client is still never accepted.** Core decides; a client may
    ask. That asymmetry is what keeps Invariant 1 intact once the direction exists —
    it is no longer guaranteed by the absence of the path, so it has to be guaranteed
    by what Core will and will not accept.
    """
    message = _require_object(raw)
    if message.get("v") != PROTOCOL_VERSION:
        # `hello` already pins the version for the connection, so this is a client that
        # contradicts itself mid-stream. **Answered the same way as a wrong hello** —
        # a frame whose meaning is not agreed on is not one to guess at (ADR-022)
        raise ProtocolError(f"Protocol version mismatch: {message.get('v')!r}")
    kind = message.get("kind")
    if kind == "request":
        return _parse_request(message)
    if kind != "result":
        raise ProtocolError(f"Unsupported kind from client: {kind!r}")

    corr_id = message.get("corr_id")
    if not isinstance(corr_id, str) or not corr_id:
        raise ProtocolError("Missing corr_id")

    ok = message.get("ok")
    if not isinstance(ok, bool):
        raise ProtocolError("ok must be a boolean")

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be an object")

    error = message.get("error")
    if error is not None and not isinstance(error, str):
        raise ProtocolError("error must be a string")
    if not ok and not error:
        raise ProtocolError("Failed result missing error")

    return Result(corr_id=corr_id, ok=ok, payload=payload, error=error)


def _parse_request(message: dict[str, Any]) -> Request:
    """Reads a `request` frame. **Never checks whether the method is allowed** — that is
    the server's registry (ADR-028), and keeping it there means the allowlist lives in
    exactly one place.
    """
    request_id = message.get("id")
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolError("Missing id")

    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise ProtocolError("Missing method")

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be an object")

    return Request(id=request_id, method=method, payload=payload)
