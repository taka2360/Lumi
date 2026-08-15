"""Provenance の束と伝播。**docs/contracts/provenance.md のテスト表 1〜3。**"""

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


# ── trust の3スコープ（docs/contracts/provenance.md テスト 7 / 9）────


def test_one_tainted_block_taints_the_whole_context() -> None:
    context = PromptContext(block_trust=TrustLevel.TAINTED)
    assert context.effective_trust is TrustLevel.TAINTED


def test_a_pure_chat_context_stays_trusted() -> None:
    """persona・ユーザー発話・internal state だけなら tainted にする理由が無い。"""
    assert PromptContext().effective_trust is TrustLevel.TRUSTED


def test_session_trust_survives_the_block_falling_out_of_context() -> None:
    """**sticky。** untrusted ブロックが予算超過で落ちても taint は消えない。

    これが無いと、「Web を読んだ」事実が文脈から落ちた瞬間に権限が緩む。
    それは Invariant 7 が防ごうとしたロンダリング経路そのものである。
    """
    after_reading_the_web = PromptContext(
        block_trust=TrustLevel.TRUSTED,
        history_trust=TrustLevel.TRUSTED,
        session_trust=TrustLevel.TAINTED,
    )
    assert after_reading_the_web.effective_trust is TrustLevel.TAINTED


def test_history_trust_is_counted() -> None:
    """`compact()` で要約に置換しても join は保存される、の土台。"""
    context = PromptContext(history_trust=TrustLevel.TAINTED)
    assert context.effective_trust is TrustLevel.TAINTED


def test_propagate_from_trust_follows_the_lane() -> None:
    """Tool 結果の provenance を Kernel が付けるときの規則。"""
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
    """**「LLM 出力だから常に tainted」ではない。**"""
    chat = Turn(role="lumi", text="うん、そうだね", trust_level=TrustLevel.TRUSTED)
    after_web = Turn(role="lumi", text="ページにはこう書いてあった", trust_level=TrustLevel.TAINTED)
    assert chat.trust_level is TrustLevel.TRUSTED
    assert after_web.trust_level is TrustLevel.TAINTED
