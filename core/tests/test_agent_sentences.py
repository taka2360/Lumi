"""文分割。**最初の音が出るまでの時間が体感のほぼすべて**（docs/architecture/audio.md §6）。"""

from __future__ import annotations

from lumi.agent.sentences import MAX_CHARS, SentenceStream, is_speakable


def test_sentences_are_emitted_at_terminators() -> None:
    stream = SentenceStream()
    assert stream.feed("こんにちは。げんき？") == ["こんにちは。", "げんき？"]


def test_an_incomplete_sentence_waits() -> None:
    """**途中で切って喋らない。** 細切れの音声はイントネーションが壊れる。"""
    assert SentenceStream().feed("まだ途中") == []


def test_a_sentence_split_across_chunks() -> None:
    stream = SentenceStream()
    assert stream.feed("きょ") == []
    assert stream.feed("うはいい天気。") == ["きょうはいい天気。"]


def test_the_closing_mark_stays_with_the_sentence() -> None:
    """`そうだね！」` の `」` を落とさない。"""
    stream = SentenceStream()
    assert stream.feed("「そうだね！」つぎ。") == ["「そうだね！」", "つぎ。"]


def test_a_terminator_does_not_wait_for_a_possible_closer() -> None:
    """**待たない。** 待つと第1文が必ず1チャンク分遅れ、そこが体感のほぼすべてである。

    はぐれた `」` は読み上げるものが無いので落ちる。**失うものが無い方に倒す。**
    """
    stream = SentenceStream()
    assert stream.feed("やった！") == ["やった！"]
    assert stream.feed("」") == []


def test_flush_emits_the_remainder() -> None:
    """**終端が無くても喋る。** ストリームが終わったら残りは全部読む。"""
    stream = SentenceStream()
    stream.feed("句点がない")
    assert stream.flush() == ["句点がない"]


def test_flush_is_idempotent() -> None:
    stream = SentenceStream()
    stream.feed("あ。")
    stream.flush()
    assert stream.flush() == []


def test_a_long_run_is_cut_at_a_soft_break() -> None:
    """終端が来ないまま伸びたら諦めるが、**切る場所は選ぶ**。"""
    stream = SentenceStream()
    text = "あ" * 40 + "、" + "い" * 40
    sentences = stream.feed(text)

    assert sentences
    assert sentences[0].endswith("、")
    assert len(sentences[0]) <= MAX_CHARS


def test_a_long_run_without_any_break_is_cut_hard() -> None:
    stream = SentenceStream()
    sentences = stream.feed("あ" * (MAX_CHARS * 2))
    assert sentences == ["あ" * MAX_CHARS, "あ" * MAX_CHARS]


def test_newlines_end_a_sentence() -> None:
    stream = SentenceStream()
    assert stream.feed("ひとこと\nふたこと\n") == ["ひとこと", "ふたこと"]


def test_punctuation_only_fragments_are_not_spoken() -> None:
    """**読み上げるものが無い断片を TTS に投げない。**"""
    stream = SentenceStream()
    assert stream.feed("。。。") == []
    assert stream.flush() == []


def test_is_speakable() -> None:
    assert is_speakable("あ")
    assert not is_speakable("　")
    assert not is_speakable("！？")
