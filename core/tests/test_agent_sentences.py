"""Sentence splitting. **Time to first sound is almost the entire perceived experience**
(docs/architecture/audio.md §6).
"""

from __future__ import annotations

from lumi.agent.sentences import (
    FIRST_MAX_CHARS,
    MAX_CHARS,
    SentenceStream,
    is_speakable,
)


def test_sentences_are_emitted_at_terminators() -> None:
    stream = SentenceStream()
    assert stream.feed("こんにちは。げんき？") == ["こんにちは。", "げんき？"]


def test_an_incomplete_sentence_waits() -> None:
    """**Never cuts mid-sentence to speak.** Choppy audio breaks intonation."""
    assert SentenceStream().feed("まだ途中") == []


def test_a_sentence_split_across_chunks() -> None:
    stream = SentenceStream()
    assert stream.feed("きょ") == []
    assert stream.feed("うはいい天気。") == ["きょうはいい天気。"]


def test_the_closing_mark_stays_with_the_sentence() -> None:
    """The closing `」` in `そうだね！」` is never dropped."""
    stream = SentenceStream()
    assert stream.feed("「そうだね！」つぎ。") == ["「そうだね！」", "つぎ。"]


def test_a_terminator_does_not_wait_for_a_possible_closer() -> None:
    """**Never waits.** Waiting would always delay the first sentence by a full chunk,
    which is almost the entire perceived experience.

    A stray `」` gets dropped since there's nothing to speak. **Erring where there's
    nothing to lose.**
    """
    stream = SentenceStream()
    assert stream.feed("やった！") == ["やった！"]
    assert stream.feed("」") == []


def test_flush_emits_the_remainder() -> None:
    """**Speaks even without a terminator.** Once the stream ends, the entire remainder is read."""
    stream = SentenceStream()
    stream.feed("句点がない")
    assert stream.flush() == ["句点がない"]


def test_flush_is_idempotent() -> None:
    stream = SentenceStream()
    stream.feed("あ。")
    stream.flush()
    assert stream.flush() == []


def test_a_long_run_is_cut_at_a_soft_break() -> None:
    """Gives up if it grows with no terminator arriving, but **still chooses where to cut**."""
    stream = SentenceStream()
    text = "あ" * 40 + "、" + "い" * 40
    sentences = stream.feed(text)

    assert sentences
    assert sentences[0].endswith("、")
    assert len(sentences[0]) <= MAX_CHARS


def test_a_long_run_without_any_break_is_cut_hard() -> None:
    stream = SentenceStream()
    sentences = stream.feed("あ" * (FIRST_MAX_CHARS + MAX_CHARS * 2))
    # The first is capped short; the rest use the normal cap
    assert sentences == ["あ" * FIRST_MAX_CHARS, "あ" * MAX_CHARS, "あ" * MAX_CHARS]


# ── The first segment is cut short (docs/architecture/audio.md §6) ──────────


def test_the_first_segment_cuts_at_a_comma() -> None:
    """**Time to first sound beats intonation — once per utterance.**

    Waiting for "。" delays the reply's very first sound, and that delay happens exactly
    where it is felt most.
    """
    stream = SentenceStream()
    fed = stream.feed("そうだね、たぶんそうなると思うよ。")
    assert fed == ["そうだね、", "たぶんそうなると思うよ。"]


def test_later_segments_do_not_cut_at_a_comma() -> None:
    """**Only the first one.** Chopping every clause would wreck the intonation throughout."""
    stream = SentenceStream()
    stream.feed("うん。")
    assert stream.feed("あとでね、たぶん。") == ["あとでね、たぶん。"]


def test_the_first_segment_is_capped_shorter() -> None:
    stream = SentenceStream()
    first = stream.feed("あ" * FIRST_MAX_CHARS)[0]
    assert len(first) == FIRST_MAX_CHARS


def test_a_terminator_still_wins_over_the_short_cap() -> None:
    """A short first sentence isn't padded out to the cap."""
    stream = SentenceStream()
    assert stream.feed("うん。ところで") == ["うん。"]


def test_an_unspeakable_first_fragment_does_not_consume_the_exception() -> None:
    """A leading "、" produces nothing speakable. **The next segment still gets the short cut.**

    If the exception were spent on a fragment that never reached TTS, the real first
    sentence would be cut on the normal rule and the whole point would be lost.
    """
    stream = SentenceStream()
    assert stream.feed("、そうだね、うん。") == ["そうだね、", "うん。"]


def test_newlines_end_a_sentence() -> None:
    stream = SentenceStream()
    assert stream.feed("ひとこと\nふたこと\n") == ["ひとこと", "ふたこと"]


def test_punctuation_only_fragments_are_not_spoken() -> None:
    """**Never sends a fragment with nothing to speak to TTS.**"""
    stream = SentenceStream()
    assert stream.feed("。。。") == []
    assert stream.flush() == []


def test_is_speakable() -> None:
    assert is_speakable("あ")
    assert not is_speakable("　")
    assert not is_speakable("！？")
