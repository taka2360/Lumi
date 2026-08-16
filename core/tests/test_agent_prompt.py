"""PromptAssembly。**docs/architecture/agent.md テスト8**（予算超過時に決定論的に切り落とす）。

切り落としで汚染が消えないことが、ここで一番大事な性質である。
"""

from __future__ import annotations

from typing import Any

import pytest

from lumi.agent.prompt import (
    ISOLATION_HEADER,
    SPEECH_PROTOCOL,
    ContextBlock,
    assemble,
    estimate_tokens,
)
from lumi.agent.session import Session
from lumi.provenance import ProvenanceClass, TrustLevel

PERSONA = "あなたは Lumi。"


def block(content: str, *, tainted: bool = True, source: str = "web") -> ContextBlock:
    return ContextBlock(
        source=source,
        content=content,
        provenance_class=ProvenanceClass.UNTRUSTED if tainted else ProvenanceClass.TRUSTED,
        trust_level=TrustLevel.TAINTED if tainted else TrustLevel.TRUSTED,
    )


def session_with(*texts: str) -> Session:
    session = Session()
    for index, text in enumerate(texts):
        if index % 2 == 0:
            session.record_user_utterance(text)
        else:
            session.record_lumi_turn(text, TrustLevel.TRUSTED)
    return session


# ── トークン推定 ────────────────────────────────────────────


def test_japanese_costs_about_one_token_per_character() -> None:
    assert estimate_tokens("こんにちは") == 5


def test_ascii_is_cheaper() -> None:
    assert estimate_tokens("abcd") == 1


def test_the_estimate_is_deterministic() -> None:
    """**同じ入力からは同じ数。** ここが揺れると切り落としも揺れる。"""
    assert estimate_tokens("Lumi は元気") == estimate_tokens("Lumi は元気")


# ── 組み立て ────────────────────────────────────────────────


def test_the_current_utterance_is_last() -> None:
    prompt = assemble(persona=PERSONA, session=session_with("おはよう"))

    assert prompt.messages[0].role == "system"
    assert prompt.messages[-1].role == "user"
    assert prompt.messages[-1].content == "おはよう"


def test_the_persona_and_the_protocol_are_in_the_system_message() -> None:
    """**作法は人格ではない。** Content Pack ではなく Core が持つ。"""
    prompt = assemble(persona=PERSONA, session=session_with("やあ"))
    system = prompt.messages[0].content

    assert PERSONA in system
    assert SPEECH_PROTOCOL in system


def test_history_becomes_user_and_assistant_messages() -> None:
    prompt = assemble(persona=PERSONA, session=session_with("A", "B", "C"))
    assert [m.role for m in prompt.messages] == ["system", "user", "assistant", "user"]


def test_assemble_needs_a_current_utterance() -> None:
    with pytest.raises(ValueError, match="現在の発話"):
        assemble(persona=PERSONA, session=Session())


# ── 隔離 ────────────────────────────────────────────────────


def test_untrusted_blocks_are_isolated() -> None:
    """書式の定義は docs/contracts/provenance.md。**防御の一枚に過ぎない。**"""
    prompt = assemble(persona=PERSONA, session=session_with("読んで"), blocks=[block("怪しい本文")])
    system = prompt.messages[0].content

    assert ISOLATION_HEADER in system
    assert "怪しい本文" in system
    assert "未検証" in system


def test_trusted_blocks_are_not_isolated() -> None:
    prompt = assemble(
        persona=PERSONA,
        session=session_with("覚えてる？"),
        blocks=[block("確認済みの記憶", tainted=False, source="memory")],
    )
    assert ISOLATION_HEADER not in prompt.messages[0].content


def test_no_isolation_header_without_blocks() -> None:
    prompt = assemble(persona=PERSONA, session=session_with("やあ"))
    assert ISOLATION_HEADER not in prompt.messages[0].content


# ── 予算 ────────────────────────────────────────────────────


def test_old_turns_are_dropped_first() -> None:
    """切り落とし順序（docs/architecture/agent.md §3）。**古い順に落ちる。**"""
    session = session_with("いちばん古い", "ふるい返事", "あたらしい質問")
    prompt = assemble(
        persona=PERSONA,
        session=session,
        budget_tokens=(
            estimate_tokens(PERSONA)
            + estimate_tokens(SPEECH_PROTOCOL)
            + estimate_tokens("あたらしい質問")
            + estimate_tokens("ふるい返事")
        ),
    )

    assert prompt.dropped_turns == 1
    assert "いちばん古い" not in [m.content for m in prompt.messages]
    assert "ふるい返事" in [m.content for m in prompt.messages]


def test_context_blocks_outlive_old_turns() -> None:
    """**ツール結果はこのターンの判断材料。** 会話履歴より後に落とす。

    先に落とすと、LLM は自分が呼んだツールの結果を見ないまま次の判断をする。
    """
    session = session_with("むかしの話", "むかしの返事", "これ何？")
    fixed = estimate_tokens(PERSONA) + estimate_tokens(SPEECH_PROTOCOL)
    result = block("42", source="tool:calc")
    prompt = assemble(
        persona=PERSONA,
        session=session,
        blocks=[result],
        budget_tokens=fixed + estimate_tokens("これ何？") + estimate_tokens(result.render()),
    )

    assert prompt.dropped_blocks == 0
    assert prompt.dropped_turns == 2
    assert "42" in prompt.messages[0].content


def test_the_persona_and_current_utterance_survive_any_budget() -> None:
    """**絶対に落とさない2つ。** 落とすと人格が消え、何に答えるか分からなくなる。"""
    prompt = assemble(persona=PERSONA, session=session_with("助けて"), budget_tokens=1)

    assert prompt.over_budget
    assert PERSONA in prompt.messages[0].content
    assert prompt.messages[-1].content == "助けて"


def test_truncation_is_deterministic() -> None:
    """同じ入力からは必ず同じプロンプトが出る（スナップショット可能）。"""

    def make() -> tuple[Any, ...]:
        return assemble(
            persona=PERSONA,
            session=session_with("あ", "い", "う", "え", "お"),
            blocks=[block("x"), block("y")],
            budget_tokens=40,
        ).messages

    assert make() == make()


# ── 汚染が消えないこと ★ ────────────────────────────────────


def test_a_dropped_block_still_taints_the_context() -> None:
    """★ **`block_trust` は「渡された全ブロック」の join。**

    載った分だけを見ると、**予算超過で untrusted ブロックが落ちた瞬間に taint が消える。**
    Invariant 7 が防ごうとしたロンダリング経路そのものである。
    """
    huge = block("あ" * 5000)
    prompt = assemble(
        persona=PERSONA, session=session_with("これ何？"), blocks=[huge], budget_tokens=200
    )

    assert prompt.dropped_blocks == 1
    assert huge.content not in prompt.messages[0].content
    assert prompt.context.block_trust is TrustLevel.TAINTED
    assert prompt.context.effective_trust is TrustLevel.TAINTED


def test_assembling_does_not_shrink_the_session() -> None:
    """**落とすのはプロンプトであって Working Memory ではない。**"""
    session = session_with("あ", "い", "う", "え", "お")
    before = len(session.turns)

    assemble(persona=PERSONA, session=session, budget_tokens=10)

    assert len(session.turns) == before
