"""Canonicalizer / BindVerifier / ResultVerifier — **all owned by the Kernel.**

Contract → docs/contracts/tool-execution.md / Types → docs/interfaces/tool.md

## Why these don't live on the Tool side

`Tool.bind` is implemented by the Tool. If a Tool returns a Handle for a different
target than its scope (whether maliciously or by mistake), `execute` would operate on
that wrong target.

**`BindVerifier` is owned by the Kernel and independently verifies that the Handle a
Tool returned actually points at its scope.** Without this, the contract degrades into
"trust the Tool."

## Phase 1's scope

Only the `character` lane. **`fs` arrives in Phase 4a, `browser` in 4b, `input` in 4c.**
The real implementation (realpath / traversal / UNC / IDN / pre-resolving redirects)
is Phase 4a.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from lumi.permission.scope import Handle, ScopeLane, SecurityScope


class CanonicalizationError(ValueError):
    """Normalization failed. **Results in `deny`** (never a "not sure, so let it through" path)."""


class BindVerificationError(RuntimeError):
    """The Handle doesn't point at the scope. **`execute` never runs.**"""


class ResultVerificationError(RuntimeError):
    """Class B reported operating outside its scope. **The result is discarded** (the side effect itself can't be undone)."""


class Canonicalizer(Protocol):
    lane: ScopeLane

    def canonicalize(self, raw_input: Mapping[str, Any]) -> SecurityScope:
        """Raises `CanonicalizationError` on failure (fail-closed)."""
        ...


class BindVerifier(Protocol):
    """Class A. **Verifies before execute.** Never lets the side effect happen."""

    lane: ScopeLane

    def verify(self, scope: SecurityScope, handle: Handle) -> None:
        """Raises `BindVerificationError` on mismatch."""
        ...


class ResultVerifier(Protocol):
    """Class B. **After invoke.** The side effect may have already happened."""

    lane: ScopeLane

    def verify(self, scope: SecurityScope, acted_on: str) -> None:
        """Raises `ResultVerificationError` if outside scope. The result is discarded and recorded as `denied`."""
        ...


#: The `character` lane only ever has one target: Lumi's own character.
CHARACTER_SELF = "character:self"


class CharacterCanonicalizer:
    """The `character` lane. **The target is always Lumi itself.**

    The kind of expression (`emotion`) is never put in `canonical`. Scope means "**what
    authority is over**," not "what action is taken." Including emotion in the scope
    would make a Grant granular down to "happy is allowed but sad isn't," which is
    meaningless.
    """

    lane = ScopeLane.CHARACTER

    def canonicalize(self, raw_input: Mapping[str, Any]) -> SecurityScope:
        """Canonicalizes the target, and **carries the operation's parameters riding on the scope.**

        `execute` only reads `handle.scope` (never re-resolving raw input — the defense
        against TOCTOU), so arguments have to ride along on the immutable scope.

        **Parameter validity is not checked here.** That's domain knowledge and the
        Tool's job. All the Canonicalizer does is settle "**what target is this
        operation over**."
        """
        target = raw_input.get("target", "self")
        if target != "self":
            # There's no concept of "another character." **An unrecognized target is never let through.**
            raise CanonicalizationError(f"未知の対象: {target!r}")
        return SecurityScope(
            lane=ScopeLane.CHARACTER,
            canonical=CHARACTER_SELF,
            metadata={key: value for key, value in raw_input.items() if key != "target"},
        )


class CharacterBindVerifier:
    """The `character` lane.

    **To be honest, there isn't much substance to verify in this lane.** There's
    nothing equivalent to `fs`'s `fstat` or `input`'s `WindowFromPoint`; all that can
    be confirmed is "does the scope the Handle claims match the scope Policy inspected."

    It's still here **to keep the path identical to production** (Phase 1's goal).
    Having this owned by the Kernel is the precondition for adding `fs` in Phase 4a.
    """

    lane = ScopeLane.CHARACTER

    def verify(self, scope: SecurityScope, handle: Handle) -> None:
        if handle.scope != scope:
            raise BindVerificationError(
                f"handle の scope が違う: {handle.scope.canonical!r} != {scope.canonical!r}"
            )
