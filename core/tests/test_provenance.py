"""Provenance の束と伝播。**docs/contracts/provenance.md のテスト表 1〜3。**"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from lumi.provenance import (
    ProvenanceClass,
    TrustLevel,
    join,
    join_all,
    propagate,
    propagate_trust,
    taint,
)


@dataclass(frozen=True)
class Block:
    """`Provenanced` を満たす最小のもの。"""

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
    """**これが Invariant 7 の核心。** 要約は「安全になった」ことを意味しない。"""
    assert taint(ProvenanceClass.DERIVED) is TrustLevel.TAINTED
    assert taint(ProvenanceClass.UNTRUSTED) is TrustLevel.TAINTED
    assert taint(ProvenanceClass.TRUSTED) is TrustLevel.TRUSTED


def test_join_all_of_nothing_is_trusted() -> None:
    """単位元。**入力が無いことは、汚染されていないことを意味する。**"""
    assert join_all([]) is TrustLevel.TRUSTED


def test_output_of_a_process_containing_untrusted_is_tainted() -> None:
    inputs = [block(ProvenanceClass.TRUSTED), block(ProvenanceClass.UNTRUSTED)]
    assert propagate(inputs, is_raw_external=False) is ProvenanceClass.DERIVED
    assert propagate_trust(inputs) is TrustLevel.TAINTED


def test_output_of_a_process_containing_derived_stays_tainted() -> None:
    """**derived を混ぜても格下げされない。** ここが緩むとロンダリング経路ができる。"""
    inputs = [block(ProvenanceClass.TRUSTED), block(ProvenanceClass.DERIVED)]
    assert propagate(inputs, is_raw_external=False) is ProvenanceClass.DERIVED
    assert propagate_trust(inputs) is TrustLevel.TAINTED


def test_output_of_trusted_only_inputs_stays_trusted() -> None:
    """雑談ターン（persona + ユーザー発話 + internal state）が tainted にならないこと。"""
    inputs = [block(ProvenanceClass.TRUSTED), block(ProvenanceClass.TRUSTED)]
    assert propagate(inputs, is_raw_external=False) is ProvenanceClass.TRUSTED
    assert propagate_trust(inputs) is TrustLevel.TRUSTED


def test_raw_external_is_untrusted_even_with_trusted_inputs() -> None:
    """外から取ってきた生データは、何と混ざっていても untrusted。"""
    inputs = [block(ProvenanceClass.TRUSTED)]
    assert propagate(inputs, is_raw_external=True) is ProvenanceClass.UNTRUSTED
