"""PromptAssembly. **docs/architecture/agent.md test 8**
(deterministic truncation on budget overflow).

The single most important property here is that truncation never makes taint disappear.
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


# ── Token estimation ────────────────────────────────────────────


def test_japanese_costs_about_one_token_per_character() -> None:
    assert estimate_tokens("こんにちは") == 5


def test_ascii_is_cheaper() -> None:
    assert estimate_tokens("abcd") == 1


def test_the_estimate_is_deterministic() -> None:
    """**The same input always gives the same count.** If this wavered, truncation would too."""
    assert estimate_tokens("Lumi は元気") == estimate_tokens("Lumi は元気")


# ── Assembly ────────────────────────────────────────────────


def test_the_current_utterance_is_last() -> None:
    prompt = assemble(persona=PERSONA, session=session_with("おはよう"))

    assert prompt.messages[0].role == "system"
    assert prompt.messages[-1].role == "user"
    assert prompt.messages[-1].content == "おはよう"


def test_the_persona_and_the_protocol_are_in_the_system_message() -> None:
    """**Conventions aren't persona.** Held by Core, not the Content Pack."""
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


# ── Isolation ────────────────────────────────────────────────────


def test_untrusted_blocks_are_isolated() -> None:
    """Format defined in docs/contracts/provenance.md. **Only one layer of defense.**"""
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


# ── Budget ────────────────────────────────────────────────────


def test_old_turns_are_dropped_first() -> None:
    """Truncation order (docs/architecture/agent.md §3). **Dropped oldest-first.**"""
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
    """**Tool results are input to this turn's decision.** Dropped after conversation history.

    Dropping them first would leave the LLM deciding its next move without seeing
    the results of tools it called.
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
    """**The two things that are never dropped.** Dropping either would erase the persona or leave
    it unclear what's being answered.
    """
    prompt = assemble(persona=PERSONA, session=session_with("助けて"), budget_tokens=1)

    assert prompt.over_budget
    assert PERSONA in prompt.messages[0].content
    assert prompt.messages[-1].content == "助けて"


def test_truncation_is_deterministic() -> None:
    """The same input always produces the same prompt (snapshottable)."""

    def make() -> tuple[Any, ...]:
        return assemble(
            persona=PERSONA,
            session=session_with("あ", "い", "う", "え", "お"),
            blocks=[block("x"), block("y")],
            budget_tokens=40,
        ).messages

    assert make() == make()


# ── Taint never disappears * ────────────────────────────────


def test_a_dropped_block_still_taints_the_context() -> None:
    """* **`block_trust` is the join of "all blocks passed in."**

    Looking only at what fit would let **taint vanish the instant an untrusted block
    drops from budget overflow.** Exactly the laundering path Invariant 7 exists to prevent.
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
    """**What's truncated is the prompt, not Working Memory.**"""
    session = session_with("あ", "い", "う", "え", "お")
    before = len(session.turns)

    assemble(persona=PERSONA, session=session, budget_tokens=10)

    assert len(session.turns) == before
