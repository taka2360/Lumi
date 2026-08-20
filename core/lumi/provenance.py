"""Trust tracking and propagation — a type-level implementation of
[Invariant 3](../../docs/contracts/invariants.md) / 7.

The single source of definition for these rules → docs/contracts/provenance.md

## Why this lives at the top level

`Signal` carries a `trust_level` (docs/contracts/event-model.md), so `kernel/` depends
on this type. Placing it under `memory/` would create a **kernel → memory reverse
dependency**, breaking "kernel depends on nothing else."

The trust types are a **cross-cutting constraint used by all of** kernel, permission,
tools, agent, and memory, and placing them under any single one of those would make it
look like that module owns them.
→ docs/architecture/core.md §4

## Absolute implementation rules

**`TrustLevel.TRUSTED` may be assigned in exactly two places.**

1. The handler that receives direct user input (voice input, text input, UI actions)
2. The memory UI's user-confirmation handler (Phase 2)

**No automatic-escalation implementation is ever built.** The `join` / `taint` /
`propagate` functions here **propagate taint — they are not an escalation path**
(they return trusted only when every input is trusted). This is verified with grep and tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol


class ProvenanceClass(StrEnum):
    """A label. **For audit and explaining to the user** — not for relaxing Policy."""

    #: Direct user input / Lumi's internal state / system settings / user_confirmed memories
    TRUSTED = "trusted"
    # : Raw data of external origin. Web page content, files, Vision results, game screens,
    # Extension output
    UNTRUSTED = "untrusted"
    # : Output of processing that took untrusted content as input. Summaries, extracted memories,
    # inference results
    DERIVED = "derived"


class TrustLevel(StrEnum):
    """For Policy decisions. A join-semilattice (`trusted ⊑ tainted`)."""

    TRUSTED = "trusted"
    TAINTED = "tainted"


class Provenanced(Protocol):
    """A record that carries provenance. **The granularity is per-record, not per-character.**"""

    @property
    def provenance_class(self) -> ProvenanceClass: ...

    @property
    def trust_level(self) -> TrustLevel: ...


def taint(cls: ProvenanceClass) -> TrustLevel:
    """Collapses the label into the binary value Policy decides on.

    **`DERIVED` also becomes `TAINTED`.** This is the core of Invariant 7, and it
    rejects the intuition that "a summary of a malicious page seems safer than the raw
    page." An attacker can craft a payload that survives summarization, and allowing a
    downgrade would create a laundering path.
    """
    return TrustLevel.TRUSTED if cls is ProvenanceClass.TRUSTED else TrustLevel.TAINTED


def join(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """Join over the lattice. **Tainted if either side is tainted.**"""
    if a is TrustLevel.TAINTED or b is TrustLevel.TAINTED:
        return TrustLevel.TAINTED
    return TrustLevel.TRUSTED


def join_all(levels: Iterable[TrustLevel]) -> TrustLevel:
    """`TRUSTED` if empty. **No input means no taint** (identity element)."""
    result = TrustLevel.TRUSTED
    for level in levels:
        result = join(result, level)
    return result


def propagate(inputs: Iterable[Provenanced], *, is_raw_external: bool) -> ProvenanceClass:
    """Decides the `ProvenanceClass` to attach to a processing step's output. **When in
    doubt, err toward taint.**

    `is_raw_external=True` means "**raw data fetched from outside Lumi's world**." This
    must never be set on LLM output — LLM output is a function of the prompt Lumi
    assembled, not an observation of the outside world. Setting it there would taint
    even ordinary small-talk turns and rob the escalation rule of its discriminating power.
    """
    if is_raw_external:
        return ProvenanceClass.UNTRUSTED
    if all(i.provenance_class is ProvenanceClass.TRUSTED for i in inputs):
        return ProvenanceClass.TRUSTED
    return ProvenanceClass.DERIVED


def propagate_trust(inputs: Iterable[Provenanced]) -> TrustLevel:
    """The `TrustLevel` of a processing step's output. The join of its inputs."""
    return join_all(i.trust_level for i in inputs)


def propagate_from_trust(trust: TrustLevel, *, is_raw_external: bool) -> ProvenanceClass:
    """`propagate()` for when only the input's `TrustLevel` is on hand.

    Used when the Kernel attaches provenance to a Tool result (the only input is the
    caller context's single `effective_trust`). **The `is_raw_external` decision
    belongs to the lane** (`lumi.permission.scope.LANE_RESULT_IS_EXTERNAL`). The Tool
    never self-declares it.
    """
    if is_raw_external:
        return ProvenanceClass.UNTRUSTED
    return ProvenanceClass.TRUSTED if trust is TrustLevel.TRUSTED else ProvenanceClass.DERIVED


@dataclass(frozen=True, slots=True)
class Turn:
    """One turn of conversation.

    **A Lumi turn's `trust_level` is the join of the inputs used to generate it.**
    Not "always tainted because it's LLM output." A pure small-talk turn is generated
    solely from persona, the user's utterance, and internal state, with no reason to
    treat it as tainted.
    """

    role: Literal["user", "lumi"]
    text: str
    trust_level: TrustLevel


@dataclass(frozen=True, slots=True)
class PromptContext:
    """The prompt's overall effective trust. **Split into three scopes.**

    Without splitting, the design forks into two options and neither is acceptable.

    - Joining past turns at the same level as blocks
      → **every turn from the second one onward is always TAINTED**, and the
      escalation rule loses its discriminating power
    - Excluding past turns from the join
      → a web summary enters the next turn as "my own utterance," and **taint
      vanishes right there**
    """

    #: Join of this turn's ContextBlocks (tool results, memories)
    block_trust: TrustLevel = TrustLevel.TRUSTED
    #: Join of the Turns currently in Working Memory
    history_trust: TrustLevel = TrustLevel.TRUSTED
    #: The full join since session start. **sticky** (once TAINTED, never reverts)
    session_trust: TrustLevel = TrustLevel.TRUSTED

    @property
    def effective_trust(self) -> TrustLevel:
        """**Never called `max_provenance`.** What "maximum" means here is ambiguous and always gets
        misread.
        """
        return join(self.block_trust, join(self.history_trust, self.session_trust))
