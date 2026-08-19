"""`SecurityScope` / `ScopeLane` / `Handle`.

Type definitions → docs/interfaces/tool.md / Contract → docs/contracts/tool-execution.md

**Policy is applied only to `SecurityScope`. Never to raw arguments.**

```
Raw Input
   ↓  Canonicalizer[lane]   ← owned by Kernel
SecurityScope (immutable)
   ↓  Policy.decide
Decision
```

`lane` lives in `permission/` because **the Canonicalizer and the verifiers are owned
by the Kernel** (docs/interfaces/tool.md: "these live in `permission/`, not `tools/`").
`tools/` may import `permission/`, never the reverse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol


class ScopeLane(StrEnum):
    """Which Canonicalizer / verifier to use. **The Class is also determined by the lane.**"""

    # ── Class A: in-core only. Handle contract ──────
    FS = "fs"
    PROCESS = "process"
    INPUT = "input"
    #: Reads such as screenshots
    DESKTOP = "desktop"
    SYSTEM = "system"
    #: Lumi's memory operations
    MEMORY = "memory"
    #: Expressions and motion
    CHARACTER = "character"

    # ── Class B: out-of-process. Post-hoc verification contract ───
    BROWSER = "browser"
    GAME = "game"
    WIDGET = "widget"


class ToolClass(StrEnum):
    """`BindVerifier` only holds when the Handle lives inside Core's own process (ADR-017)."""

    #: canonicalize → decide → **bind → verify** → execute
    A = "A"
    #: canonicalize → decide → invoke → **verify_result** (TOCTOU can't be prevented, only detected)
    B = "B"


#: **Owned by the Kernel. Tools never declare their own Class** (no self-reporting allowed).
LANE_CLASS: Final[dict[ScopeLane, ToolClass]] = {
    ScopeLane.FS: ToolClass.A,
    ScopeLane.PROCESS: ToolClass.A,
    ScopeLane.INPUT: ToolClass.A,
    ScopeLane.DESKTOP: ToolClass.A,
    ScopeLane.SYSTEM: ToolClass.A,
    ScopeLane.MEMORY: ToolClass.A,
    ScopeLane.CHARACTER: ToolClass.A,
    ScopeLane.BROWSER: ToolClass.B,
    ScopeLane.GAME: ToolClass.B,
    ScopeLane.WIDGET: ToolClass.B,
}


#: **The Kernel decides per lane whether the result is raw data that came from outside Lumi.**
#: Tools never self-declare this (`ToolResult`'s provenance is attached by Core).
#:
#: `character` is set to False because changing an expression isn't an observation of
#: the outside world. Setting it True would **taint the session every time an
#: expression changes**, and the provenance escalation rule (effective L3+ → ask)
#: would lose its discriminating power.
LANE_RESULT_IS_EXTERNAL: Final[dict[ScopeLane, bool]] = {
    ScopeLane.FS: True,
    ScopeLane.PROCESS: True,
    #: Only returns whether the injection succeeded — carries back nothing from the outside world
    ScopeLane.INPUT: False,
    #: A screenshot is the outside world itself
    ScopeLane.DESKTOP: True,
    ScopeLane.SYSTEM: True,
    #: Phase 2. How `user_confirmed` memories are treated is decided on the MemoryStore side
    ScopeLane.MEMORY: False,
    ScopeLane.CHARACTER: False,
    ScopeLane.BROWSER: True,
    ScopeLane.GAME: True,
    ScopeLane.WIDGET: True,
}


@dataclass(frozen=True, slots=True)
class SecurityScope:
    """**Immutable.** If it could be rewritten after Policy inspects it, TOCTOU becomes possible.

    Only `Canonicalizer` (owned by the Kernel) can construct one. Neither Tools nor the LLM can.

    `frozen=True` only stops the fields being rebound — **it does not freeze what they point
    at.** `metadata` carries the operation's parameters (`verifiers.CharacterCanonicalizer`),
    it is what `execute` reads instead of re-resolving raw input, and it comes from the LLM's
    tool call. A `dict` there would be writable between `decide` and `execute`, which is the
    exact window this type exists to close, so it is **copied and frozen on construction.**
    """

    lane: ScopeLane
    #: The canonicalized target (absolute path / normalized URL / resolved executable path)
    canonical: str
    #: Lane-specific (HWND, PID, etc). **Shows up in audit logs, so never put secrets here**
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # `slots=True` + `frozen=True`: assignment has to go around the frozen guard
        object.__setattr__(self, "metadata", _freeze(self.metadata))


def _freeze(value: Any) -> Any:
    """Returns a read-only copy. **Recursive** — a nested `dict` or `list` left writable
    would reopen the same window one level down.

    `str` and `bytes` are `Sequence`s but already immutable, so they are returned as-is.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, str | bytes):
        return value
    if isinstance(value, Set):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, Sequence):
        return tuple(_freeze(item) for item in value)
    return value


class Handle(Protocol):
    """A stable reference to whatever a `SecurityScope` points at.

    **A Handle isn't valid until it passes `BindVerifier.verify()`.** The Handle `Tool.bind` returns
    must never be passed straight to `execute` (the Kernel sits in between).
    """

    @property
    def scope(self) -> SecurityScope: ...

    def close(self) -> None: ...
