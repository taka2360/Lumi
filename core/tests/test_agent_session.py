"""会話セッションと trust の3スコープ。**docs/contracts/provenance.md テスト 8 / 9 / 10 / 11。**

ここが Invariant 7（No Laundering）の要である。汚染が消える経路を1つでも作ると、
「Web を読んだあとの Lumi の発話」から taint が抜けて、次のターンで L3 が素通りする。
"""

from __future__ import annotations

import pytest

from lumi.agent.session import Session
from lumi.provenance import TrustLevel


def test_a_new_session_starts_trusted() -> None:
    """**テスト11。** セッションを跨いで汚染を持ち越さない（provenance.md 規則5）。

    これが「文脈を切れば汚染から抜けられる」というユーザーの逃げ道になっている。
    """
    assert Session().session_trust is TrustLevel.TRUSTED


def test_a_small_talk_turn_stays_trusted() -> None:
    """**テスト8。** persona とユーザー発話だけで作った Lumi のターンは汚染されない。

    ここを一律 tainted にすると、**2ターン目以降が常に TAINTED** になり、
    provenance 昇格が常時発火して規則が判別力を失う。
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
    """**テスト9。** untrusted なブロックが文脈から落ちても汚染は残る。

    sticky でないと、**ブロックが予算超過で落ちた瞬間に taint が消える。**
    インジェクション文字列は LLM の中に「意図」として残るので、それでは足りない。
    """
    session = Session()
    session.record_user_utterance("このページ読んで")
    session.observe(TrustLevel.TAINTED)

    # 次のターンではブロックを渡さない（block_trust は trusted）
    session.record_lumi_turn("読んだよ", TrustLevel.TAINTED)
    session.record_user_utterance("ところで今日の天気は？")

    context = session.context(block_trust=TrustLevel.TRUSTED)
    assert context.block_trust is TrustLevel.TRUSTED
    assert context.effective_trust is TrustLevel.TAINTED


def test_compact_preserves_the_join() -> None:
    """**テスト10。** Working Memory を縮めても `history_trust` は下がらない。

    〔Phase 1 は要約せずに落とすだけ。要約への置換は Phase 2（Reflection が要る）。
    **join を保存するという契約は同じ**なので、置き換わっても振る舞いは変わらない。〕
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
    # ★ **落とした Turn の汚染が残っている**
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
    """ターンの汚染が session に伝播する。**記録だけして観測しない、が起きないように。**"""
    session = Session()
    session.record_lumi_turn("さっき読んだページによると…", TrustLevel.TAINTED)
    assert session.session_trust is TrustLevel.TAINTED


def test_user_turns_are_trusted() -> None:
    session = Session()
    turn = session.record_user_utterance("やっほー")
    assert turn.role == "user"
    assert turn.trust_level is TrustLevel.TRUSTED
