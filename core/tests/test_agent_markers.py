"""`<|ACT|>` マーカー。**docs/architecture/agent.md テスト9**（音声化テキストから除去される）。

読み上げてしまうと台無しになるので、**判定は保守的に**（怪しければ喋らない）。
"""

from __future__ import annotations

from lumi.agent.markers import MarkerStream, parse_marker
from lumi.character import Emotion


def speak(stream: MarkerStream, *chunks: str) -> str:
    """チャンク列を食わせて、**読み上げられるテキスト**だけを返す。"""
    parts = [stream.feed(chunk).text for chunk in chunks]
    parts.append(stream.flush())
    return "".join(parts)


# ── 除去 ────────────────────────────────────────────────────


def test_a_marker_is_removed_from_speech() -> None:
    stream = MarkerStream()
    assert speak(stream, 'やった<|ACT {"emotion":"happy"}|>ね') == "やったね"


def test_the_marker_becomes_an_intent() -> None:
    chunk = MarkerStream().feed('<|ACT {"emotion":"sad","intensity":0.4}|>')
    assert len(chunk.intents) == 1
    assert chunk.intents[0].emotion is Emotion.SAD
    assert chunk.intents[0].intensity == 0.4


def test_a_marker_split_across_chunks_is_still_removed() -> None:
    """**チャンク境界を跨ぐのは普通に起きる。** 留めないと途中まで喋ってしまう。"""
    stream = MarkerStream()
    assert speak(stream, 'うれ<|ACT {"emo', 'tion":"happy"}|>しい') == "うれしい"


def test_the_opening_bracket_is_held_back() -> None:
    """`<|AC` までしか来ていない時点で、それを喋らない。"""
    stream = MarkerStream()
    assert stream.feed("こんにちは<|AC").text == "こんにちは"


def test_a_bare_less_than_is_still_spoken() -> None:
    """マーカーでない `<` を飲み込まない。"""
    stream = MarkerStream()
    assert speak(stream, "3 < 5 だよ") == "3 < 5 だよ"


def test_several_markers_in_one_stream() -> None:
    stream = MarkerStream()
    chunk = stream.feed('あ<|ACT {"emotion":"happy"}|>い<|ACT {"emotion":"sad"}|>う')
    assert chunk.text == "あいう"
    assert [i.emotion for i in chunk.intents] == [Emotion.HAPPY, Emotion.SAD]


# ── 失敗したら落とす ────────────────────────────────────────


def test_broken_json_drops_the_whole_marker() -> None:
    """**中途半端に読ませない。** マーカーごと落とす。"""
    stream = MarkerStream()
    assert speak(stream, "ねえ<|ACT {emotion:happy}|>きいて") == "ねえきいて"


def test_an_unterminated_marker_is_not_spoken() -> None:
    """ストリームが途中で終わった。**「たぶんテキストだろう」で読み上げない。**"""
    stream = MarkerStream()
    assert speak(stream, 'おわり<|ACT {"emotion"') == "おわり"


def test_an_unknown_emotion_is_dropped() -> None:
    """**型に無いものを線に乗せない。**

    renderer.md の「未知の emotion はフォールバック」は、`Emotion` にはあるが
    そのモデルに表現手段が無い場合の話であって、ここの話ではない。
    """
    assert parse_marker('{"emotion":"ecstatic"}') is None


def test_a_non_object_payload_is_dropped() -> None:
    assert parse_marker("[1, 2]") is None


def test_a_missing_emotion_is_dropped() -> None:
    assert parse_marker('{"intensity":0.5}') is None


# ── 壊れた値は丸める ────────────────────────────────────────


def test_an_out_of_range_intensity_is_clamped() -> None:
    """**強度がおかしいだけで表情を失う理由が無い。**"""
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
    """`True` は `int` の部分型である。**ここで弾かないと intensity=1.0 になる。**"""
    intent = parse_marker('{"emotion":"happy","intensity":true}')
    assert intent is not None
    assert intent.intensity == 0.7
