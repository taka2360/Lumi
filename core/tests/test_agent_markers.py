"""The `<|ACT|>` marker. **docs/architecture/agent.md test 9** (stripped from spoken text).

Reading it aloud would ruin things, so **the check errs conservative** (when in doubt, don't speak it).
"""

from __future__ import annotations

from lumi.agent.markers import MarkerStream, parse_marker
from lumi.character import Emotion


def speak(stream: MarkerStream, *chunks: str) -> str:
    """Feeds a sequence of chunks and returns only **the text that gets spoken**."""
    parts = [stream.feed(chunk).text for chunk in chunks]
    parts.append(stream.flush())
    return "".join(parts)


# ── Stripping ────────────────────────────────────────────────────


def test_a_marker_is_removed_from_speech() -> None:
    stream = MarkerStream()
    assert speak(stream, 'やった<|ACT {"emotion":"happy"}|>ね') == "やったね"


def test_the_marker_becomes_an_intent() -> None:
    chunk = MarkerStream().feed('<|ACT {"emotion":"sad","intensity":0.4}|>')
    assert len(chunk.intents) == 1
    assert chunk.intents[0].emotion is Emotion.SAD
    assert chunk.intents[0].intensity == 0.4


def test_a_marker_split_across_chunks_is_still_removed() -> None:
    """**Spanning a chunk boundary happens normally.** Without holding it back, part of it would get spoken."""
    stream = MarkerStream()
    assert speak(stream, 'うれ<|ACT {"emo', 'tion":"happy"}|>しい') == "うれしい"


def test_the_opening_bracket_is_held_back() -> None:
    """Never spoken while only `<|AC` has arrived so far."""
    stream = MarkerStream()
    assert stream.feed("こんにちは<|AC").text == "こんにちは"


def test_a_bare_less_than_is_still_spoken() -> None:
    """A `<` that isn't a marker is never swallowed."""
    stream = MarkerStream()
    assert speak(stream, "3 < 5 だよ") == "3 < 5 だよ"


def test_several_markers_in_one_stream() -> None:
    stream = MarkerStream()
    chunk = stream.feed('あ<|ACT {"emotion":"happy"}|>い<|ACT {"emotion":"sad"}|>う')
    assert chunk.text == "あいう"
    assert [i.emotion for i in chunk.intents] == [Emotion.HAPPY, Emotion.SAD]


# ── Drop on failure ────────────────────────────────────────


def test_broken_json_drops_the_whole_marker() -> None:
    """**Never reads it half-parsed.** Drops the whole marker."""
    stream = MarkerStream()
    assert speak(stream, "ねえ<|ACT {emotion:happy}|>きいて") == "ねえきいて"


def test_an_unterminated_marker_is_not_spoken() -> None:
    """The stream ended mid-marker. **Never read aloud on the assumption "it's probably just text."**"""
    stream = MarkerStream()
    assert speak(stream, 'おわり<|ACT {"emotion"') == "おわり"


def test_an_unknown_emotion_is_dropped() -> None:
    """**Never puts something onto the wire that isn't in the type.**

    renderer.md's "unknown emotion falls back" refers to the case where the emotion
    exists in `Emotion` but the model has no way to express it — not what's being
    tested here.
    """
    assert parse_marker('{"emotion":"ecstatic"}') is None


def test_a_non_object_payload_is_dropped() -> None:
    assert parse_marker("[1, 2]") is None


def test_a_missing_emotion_is_dropped() -> None:
    assert parse_marker('{"intensity":0.5}') is None


# ── Clamping malformed values ────────────────────────────────────────


def test_an_out_of_range_intensity_is_clamped() -> None:
    """**No reason to lose an expression just because the intensity is off.**"""
    intent = parse_marker('{"emotion":"angry","intensity":5}')
    assert intent is not None
    assert intent.intensity == 1.0


def test_a_non_numeric_intensity_falls_back() -> None:
    intent = parse_marker('{"emotion":"angry","intensity":"つよい"}')
    assert intent is not None
    assert intent.intensity == 0.7


def test_a_negative_duration_is_treated_as_absent() -> None:
    intent = parse_marker('{"emotion":"happy","duration_ms":-1}')
    assert intent is not None
    assert intent.duration_ms is None


def test_a_boolean_is_not_a_number() -> None:
    """`True` is a subtype of `int`. **Without rejecting it here, intensity would become 1.0.**"""
    intent = parse_marker('{"emotion":"happy","intensity":true}')
    assert intent is not None
    assert intent.intensity == 0.7
