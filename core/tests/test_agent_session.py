"""The conversation session and trust's 3 scopes.
**docs/contracts/provenance.md tests 8 / 9 / 10 / 11.**

This is the crux of Invariant 7 (No Laundering). Building even one path where taint
disappears would strip it from "what Lumi says after reading the web," letting L3
pass through unchecked on the next turn.
"""

from __future__ import annotations

import pytest

from lumi.agent.session import Session
from lumi.provenance import TrustLevel


def test_a_new_session_starts_trusted() -> None:
    """**Test 11.** Taint never carries over across sessions (provenance.md rule 5).

    This is the user's escape hatch: "cutting context lets you get out of taint."
    """
    assert Session().session_trust is TrustLevel.TRUSTED


def test_a_small_talk_turn_stays_trusted() -> None:
    """**Test 8.** A Lumi turn built solely from persona and the user's utterance is never tainted.

    Making this uniformly tainted would make **every turn from the second one onward
    always TAINTED**, causing provenance escalation to fire constantly and stripping
    the rule of its discriminating power.
    """
    session = Session()
    session.record_user_utterance("おはよう")
    session.record_lumi_turn("おはよう。よく寝られた？", TrustLevel.TRUSTED)

    assert session.context().effective_trust is TrustLevel.TRUSTED


def test_a_tool_result_taints_the_session() -> None:
    session = Session()
    session.record_user_utterance("これ読んで")
    session.observe(TrustLevel.TAINTED)

    assert session.session_trust is TrustLevel.TAINTED
    assert session.context().effective_trust is TrustLevel.TAINTED


def test_the_session_trust_is_sticky() -> None:
    """**Test 9.** Taint remains even after an untrusted block drops out of context.

    Without being sticky, **taint would vanish the instant a block falls out from
    budget overflow.** An injection string lingers inside the LLM as "intent," so
    that alone isn't enough.
    """
    session = Session()
    session.record_user_utterance("このページ読んで")
    session.observe(TrustLevel.TAINTED)

    # No block passed on the next turn (block_trust is trusted)
    session.record_lumi_turn("読んだよ", TrustLevel.TAINTED)
    session.record_user_utterance("ところで今日の天気は？")

    context = session.context(block_trust=TrustLevel.TRUSTED)
    assert context.block_trust is TrustLevel.TRUSTED
    assert context.effective_trust is TrustLevel.TAINTED


def test_compact_preserves_the_join() -> None:
    """**Test 10.** `history_trust` never decreases even when Working Memory shrinks.

    [Phase 1 just drops without summarizing. Replacing this with summarization is
    Phase 2 (needs Reflection). Since **the contract of preserving the join stays the
    same**, behavior doesn't change once it's replaced.]
    """
    session = Session()
    session.record_user_utterance("ページの内容を教えて")
    session.record_lumi_turn("こう書いてあった", TrustLevel.TAINTED)
    session.record_user_utterance("ありがとう")
    assert session.history_trust is TrustLevel.TAINTED

    dropped = session.compact(keep_recent=1)

    assert dropped == 2
    assert len(session.turns) == 1
    assert session.turns[0].trust_level is TrustLevel.TRUSTED
    # * **The dropped Turn's taint remains**
    assert session.history_trust is TrustLevel.TAINTED


def test_compact_does_nothing_when_short() -> None:
    session = Session()
    session.record_user_utterance("ひとこと")
    assert session.compact(keep_recent=5) == 0
    assert len(session.turns) == 1


def test_compact_rejects_a_negative_count() -> None:
    with pytest.raises(ValueError, match="keep_recent"):
        Session().compact(keep_recent=-1)


def test_recording_a_lumi_turn_also_taints_the_session() -> None:
    """A turn's taint propagates to the session. **Prevents "recorded but not observed" from
    happening.**
    """
    session = Session()
    session.record_lumi_turn("さっき読んだページによると…", TrustLevel.TAINTED)
    assert session.session_trust is TrustLevel.TAINTED


def test_user_turns_are_trusted() -> None:
    session = Session()
    turn = session.record_user_utterance("やっほー")
    assert turn.role == "user"
    assert turn.trust_level is TrustLevel.TRUSTED
