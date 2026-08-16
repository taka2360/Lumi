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


#: The namespace Core is allowed to send for each role. **Re-read B2/B3 before adding a line here.**
NAMESPACE_BY_ROLE: Final[dict[Role, str]] = {
    Role.SHELL: "os.",
    Role.STAGE: "stage.",
}


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
        raise ProtocolError(f"JSON として読めない: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProtocolError("トップレベルがオブジェクトではない")
    return parsed


def parse_hello(raw: str) -> Hello:
    """Parses the first message right after connecting. **Raises on failure** (fail-closed)."""
    message = _require_object(raw)
    if message.get("kind") != "hello":
        raise ProtocolError("最初のメッセージが hello ではない")
    if message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError(f"プロトコルバージョンが違う: {message.get('v')!r}")

    raw_role = message.get("role")
    if not isinstance(raw_role, str):
        raise ProtocolError("role が無い")
    try:
        role = Role(raw_role)
    except ValueError as exc:
        raise ProtocolError(f"未知の role: {raw_role!r}") from exc

    token = message.get("token")
    if not isinstance(token, str) or not token:
        raise ProtocolError("token が無い")

    return Hello(role=role, token=token)


def parse_client_message(raw: str) -> Result:
    """Parses a message arriving from the client after authentication.

    Phase 0 only accepts `result`. **A `command` from the client is never accepted**
    (guaranteeing that Core is the origin of decisions by the mere absence of that path).
    """
    message = _require_object(raw)
    kind = message.get("kind")
    if kind != "result":
        raise ProtocolError(f"クライアントから受理しない kind: {kind!r}")

    corr_id = message.get("corr_id")
    if not isinstance(corr_id, str) or not corr_id:
        raise ProtocolError("corr_id が無い")

    ok = message.get("ok")
    if not isinstance(ok, bool):
        raise ProtocolError("ok が bool ではない")

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("payload がオブジェクトではない")

    error = message.get("error")
    if error is not None and not isinstance(error, str):
        raise ProtocolError("error が文字列ではない")
    if not ok and not error:
        raise ProtocolError("失敗した result に error が無い")

    return Result(corr_id=corr_id, ok=ok, payload=payload, error=error)
