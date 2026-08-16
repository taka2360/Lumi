"""Policy — **`decide()` is its sole definition.**

Single source of definition → docs/architecture/permission.md §2

> The tables and prose are all **explanations** of `decide()`, not the basis for the
> implementation. If a table and `decide()` disagree, `decide()` is correct.

## Only these four arguments

`base_risk` / `actor` / `effective_trust` / `grant`.

**The LLM's stated reasoning, a Tool's self-reported claims, and an Extension's
`reason` are never among the arguments** (Invariant 1, 3). This blocks, at the
signature level, any path for a claim like "this operation is safe" to enter the decision.

## The rule application order is fixed by the order of lines in the function

"Applied cumulatively, the strictest wins" alone doesn't settle whether provenance
escalation looks at `base_risk` or `effective_risk`. **`decide()` looks at
effective_risk** (i.e. actor escalation is applied first).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Final

from lumi.kernel.activity import Actor
from lumi.kernel.cancellation import Cancellation
from lumi.permission.grants import Grant
from lumi.provenance import TrustLevel

#: Always recorded in the audit log. **Policy changes over time.**
#: Answering "why was this operation allowed" requires knowing the rule that was in effect then.
POLICY_VERSION: Final = "2026-08-16"


class Risk(IntEnum):
    """The L assignments are Provisional. **Where the L2/L3 boundary should sit won't be known until
    it's used in practice.**
    """

    #: Reads and observation (screenshot, reading world state)
    L0 = 0
    #: Browser viewing, web search
    L1 = 1
    #: File reads, writes within the working area
    L2 = 2
    #: Launching apps, input injection, writes to arbitrary paths
    L3 = 3
    #: Shell execution, deletion, sending externally, irreversible operations
    L4 = 4
    # : **Only ever appears as an effective risk.** A Tool can never declare this (rejected at
    # registration)
    DENIED = 5


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class SideEffect(StrEnum):
    NONE = "none"
    LOCAL = "local"
    EXTERNAL = "external"
    IRREVERSIBLE = "irreversible"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """A Tool's static declaration. **A Tool only declares this — it makes no
    decisions, does no normalization, does no verification.**

    `lane` doesn't live here. Lane is a choice of execution machinery — "which
    Canonicalizer / verifier to use" — not a declaration of permission (holding it in
    both places would be a source of drift).
    """

    capability: str
    risk: Risk
    reversible: bool
    side_effect: SideEffect
    cancellation: Cancellation


def escalate_for_self_initiated(base: Risk) -> Risk:
    """Effective risk for self-initiated (autonomous) actions. **L3 and above becomes
    denial, not "escalation."**

    The description "bumps up by one level" was retracted because it contradicted the
    table at L3 (bumping L3 self up one level would give the L4/user column's `ask`,
    but the table says `deny`). Writing this as an explicit mapping removes that ambiguity.
    """
    return {
        Risk.L0: Risk.L0,  # Reads and observation are allowed even autonomously
        Risk.L1: Risk.L1,  # Browser viewing is allowed (AutonomyBudget applies separately)
        Risk.L2: Risk.L3,  # Effectively L3 = ask
        Risk.L3: Risk.DENIED,
        Risk.L4: Risk.DENIED,
        Risk.DENIED: Risk.DENIED,
    }[base]


def _evaluate(
    base_risk: Risk,
    actor: Actor,
    effective_trust: TrustLevel,
    grant: Grant | None,
) -> tuple[Decision, str]:
    """The implementation body of `decide()`. **Carries a rule identifier**
    (`policy_rule_id` in the audit log).

    To avoid writing the rules twice — once here, once in `decide()` — the decision
    logic is confined to this single place.
    """
    # ── 1. Determine the effective risk. Actor-based escalation happens exactly once, here ──
    effective_risk = base_risk
    if actor is Actor.SELF_INITIATED:
        effective_risk = escalate_for_self_initiated(base_risk)

    # ── 2. Every rule from here on applies to effective_risk ──
    if actor is Actor.SYSTEM and base_risk > Risk.L0:
        return Decision.DENY, "system_actor_is_l0_only"

    if effective_risk is Risk.DENIED:
        return Decision.DENY, "self_initiated_denied"

    if effective_trust is TrustLevel.TAINTED and effective_risk >= Risk.L3:
        return Decision.ASK, "provenance_escalation"

    if effective_risk >= Risk.L4:
        return Decision.ASK, "l4_always_asks"

    if effective_risk >= Risk.L2 and grant is None:
        return Decision.ASK, "l2_without_grant"

    return Decision.ALLOW, "allow"


def decide(
    base_risk: Risk,
    actor: Actor,
    effective_trust: TrustLevel,
    grant: Grant | None,
) -> Decision:
    """**The sole definition of Policy. Must be a pure function.**

    The only function that returns a `Decision` (.claude/rules/00-invariants.md).
    """
    return _evaluate(base_risk, actor, effective_trust, grant)[0]


def decide_with_rule(
    base_risk: Risk,
    actor: Actor,
    effective_trust: TrustLevel,
    grant: Grant | None,
) -> tuple[Decision, str]:
    """For the audit log. **The decision itself goes through the same implementation as `decide()`**
    (`_evaluate`).
    """
    return _evaluate(base_risk, actor, effective_trust, grant)
