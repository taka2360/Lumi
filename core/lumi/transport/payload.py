"""Reading fields off an inbound request. **Malformed is refused, never guessed at.**

Reason codes → docs/decisions/ADR-036-core-sends-reason-codes.md

Everything arriving over the wire is `dict[str, Any]`: a window can send a number where
a string belongs, or leave a field out entirely, and both reach the handler as data. The
failure worth designing against is not the crash — it is the **request that quietly did
something other than what it said**. A missing filter read as "no filter" answers with
the whole list; a malformed correction read as "no correction" drops what the user typed.

So: **absent and malformed are different**, and only the first has a default.

`reason` is what Stage will translate (ADR-036 — Core sends codes, never sentences). The
defaults are keyed off the field name, which is what a window needs to know which of its
fields was wrong; a handler that would rather refuse the whole request with one code
passes its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lumi.transport.router import RequestRefused


def require_str(
    payload: Mapping[str, Any], key: str, *, limit: int | None = None, reason: str | None = None
) -> str:
    """A field that must be there and must say something. **Whitespace is not something.**"""
    value = payload.get(key)
    if not isinstance(value, str):
        raise RequestRefused(reason or f"{key}_required")
    text = value.strip()
    if not text:
        raise RequestRefused(reason or f"{key}_required")
    if limit is not None and len(text) > limit:
        raise RequestRefused(reason or f"{key}_too_long")
    return text


def optional_str(
    payload: Mapping[str, Any], key: str, *, limit: int, reason: str | None = None
) -> str | None:
    """A field the window may leave out. **Left out and malformed are different things.**

    Absent — or `null`, which is how a window with nothing to say says so — means "no
    value" and the handler falls back. A number where a string belongs means the caller
    is broken, and reading that as "no value" is how a request ends up doing something
    other than what it said.
    """
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise RequestRefused(reason or f"{key}_invalid")
    text = value.strip()
    if len(text) > limit:
        raise RequestRefused(reason or f"{key}_too_long")
    return text or None


def require_bool(payload: Mapping[str, Any], key: str, *, reason: str | None = None) -> bool:
    """**Only a real bool.** Not "truthy" — `0`, `""` and `"false"` are all a caller that
    does not know what it is asking for, and guessing which way they meant it decides
    something like whether the microphone is open.
    """
    value = payload.get(key)
    if not isinstance(value, bool):
        raise RequestRefused(reason or f"{key}_required")
    return value


def require_str_map(
    payload: Mapping[str, Any], key: str, *, reason: str | None = None
) -> Mapping[str, str]:
    """An object whose keys and values are all strings.

    Checked as a whole rather than per entry: a settings write where one of five values
    is a number should apply none of them, not four.
    """
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RequestRefused(reason or f"{key}_required")
    entries: dict[str, Any] = value
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in entries.items()):
        raise RequestRefused(reason or f"{key}_invalid")
    return {str(k): str(v) for k, v in entries.items()}
