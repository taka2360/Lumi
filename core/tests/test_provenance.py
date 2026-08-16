"""The provenance lattice and its propagation. **docs/contracts/provenance.md test table 1-3.**"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from lumi.provenance import (
    PromptContext,
    ProvenanceClass,
    TrustLevel,
    Turn,
    join,
    join_all,
    propagate,
    propagate_from_trust,
    propagate_trust,
    taint,
)


@dataclass(frozen=True)
class Block:
    """The minimal thing that satisfies `Provenanced`."""

    provenance_class: ProvenanceClass
    trust_level: TrustLevel


def block(cls: ProvenanceClass) -> Block:
    return Block(cls, taint(cls))


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (TrustLevel.TRUSTED, TrustLevel.TRUSTED, TrustLevel.TRUSTED),
        (TrustLevel.TRUSTED, TrustLevel.TAINTED, TrustLevel.TAINTED),
        (TrustLevel.TAINTED, TrustLevel.TRUSTED, TrustLevel.TAINTED),
        (TrustLevel.TAINTED, TrustLevel.TAINTED, TrustLevel.TAINTED),
    ],
)
def test_join_is_correct_for_every_combination(
    a: TrustLevel, b: TrustLevel, expected: TrustLevel
) -> None:
    assert join(a, b) is expected


def test_derived_is_tainted() -> None:
    """**This is the core of Invariant 7.** A summary doesn't mean "it became safe."""
    assert taint(ProvenanceClass.DERIVED) is TrustLevel.TAINTED
    assert taint(ProvenanceClass.UNTRUSTED) is TrustLevel.TAINTED
    assert taint(ProvenanceClass.TRUSTED) is TrustLevel.TRUSTED


def test_join_all_of_nothing_is_trusted() -> None:
    """The identity element. **No input means no taint.**"""
    assert join_all([]) is TrustLevel.TRUSTED


def test_output_of_a_process_containing_untrusted_is_tainted() -> None:
    inputs = [block(ProvenanceClass.TRUSTED), block(ProvenanceClass.UNTRUSTED)]
    assert propagate(inputs, is_raw_external=False) is ProvenanceClass.DERIVED
    assert propagate_trust(inputs) is TrustLevel.TAINTED


def test_output_of_a_process_containing_derived_stays_tainted() -> None:
    """**Mixing in derived never gets downgraded.** Loosening this would create a laundering path."""
    inputs = [block(ProvenanceClass.TRUSTED), block(ProvenanceClass.DERIVED)]
    assert propagate(inputs, is_raw_external=False) is ProvenanceClass.DERIVED
    assert propagate_trust(inputs) is TrustLevel.TAINTED


def test_output_of_trusted_only_inputs_stays_trusted() -> None:
    """A small-talk turn (persona + user utterance + internal state) never becomes tainted."""
    inputs = [block(ProvenanceClass.TRUSTED), block(ProvenanceClass.TRUSTED)]
    assert propagate(inputs, is_raw_external=False) is ProvenanceClass.TRUSTED
    assert propagate_trust(inputs) is TrustLevel.TRUSTED


def test_raw_external_is_untrusted_even_with_trusted_inputs() -> None:
    """Raw data fetched from outside is untrusted no matter what it's mixed with."""
    inputs = [block(ProvenanceClass.TRUSTED)]
    assert propagate(inputs, is_raw_external=True) is ProvenanceClass.UNTRUSTED


# ── trust's 3 scopes (docs/contracts/provenance.md tests 7 / 9) ────


def test_one_tainted_block_taints_the_whole_context() -> None:
    context = PromptContext(block_trust=TrustLevel.TAINTED)
    assert context.effective_trust is TrustLevel.TAINTED


def test_a_pure_chat_context_stays_trusted() -> None:
    """With only persona, user utterance, and internal state, there's no reason to become tainted."""
    assert PromptContext().effective_trust is TrustLevel.TRUSTED


def test_session_trust_survives_the_block_falling_out_of_context() -> None:
    """**sticky.** Taint doesn't vanish even if an untrusted block drops out from budget overflow.

    Without this, permissions would loosen the instant the fact "the web was read"
    falls out of context. That's exactly the laundering path Invariant 7 exists to prevent.
    """
    after_reading_the_web = PromptContext(
        block_trust=TrustLevel.TRUSTED,
        history_trust=TrustLevel.TRUSTED,
        session_trust=TrustLevel.TAINTED,
    )
    assert after_reading_the_web.effective_trust is TrustLevel.TAINTED


def test_history_trust_is_counted() -> None:
    """The foundation for: the join is preserved even when `compact()` replaces this with a summary."""
    context = PromptContext(history_trust=TrustLevel.TAINTED)
    assert context.effective_trust is TrustLevel.TAINTED


def test_propagate_from_trust_follows_the_lane() -> None:
    """The rule for when the Kernel attaches provenance to a Tool result."""
    assert (
        propagate_from_trust(TrustLevel.TRUSTED, is_raw_external=True) is ProvenanceClass.UNTRUSTED
    )
    assert (
        propagate_from_trust(TrustLevel.TRUSTED, is_raw_external=False) is ProvenanceClass.TRUSTED
    )
    assert (
        propagate_from_trust(TrustLevel.TAINTED, is_raw_external=False) is ProvenanceClass.DERIVED
    )


def test_a_lumi_turn_inherits_the_trust_of_its_inputs() -> None:
    """**Not "always tainted because it's LLM output."**"""
    chat = Turn(role="lumi", text="うん、そうだね", trust_level=TrustLevel.TRUSTED)
    after_web = Turn(role="lumi", text="ページにはこう書いてあった", trust_level=TrustLevel.TAINTED)
    assert chat.trust_level is TrustLevel.TRUSTED
    assert after_web.trust_level is TrustLevel.TAINTED
