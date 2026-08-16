"""リップシンクのタイムライン。**純粋関数なので、エンジンを起動せずに検証できる。**

docs/interfaces/renderer.md「生成方式」/ テスト表 7（無音時に口を閉じる）
"""

from __future__ import annotations

from typing import Any

from lumi.providers.tts.viseme import Viseme, build_timeline

# 実際の audio_query（「こんにちは」/ AivisSpeech 1.2.0）と同じ形。
KONNICHIWA: dict[str, Any] = {
    "accent_phrases": [
        {
            "moras": [
                {
                    "text": "コ",
                    "consonant": "k",
                    "consonant_length": 0.06,
                    "vowel": "o",
                    "vowel_length": 0.09,
                },
                {
                    "text": "ン",
                    "consonant": None,
                    "consonant_length": None,
                    "vowel": "N",
                    "vowel_length": 0.07,
                },
                {
                    "text": "ニ",
                    "consonant": "n",
                    "consonant_length": 0.04,
                    "vowel": "i",
                    "vowel_length": 0.08,
                },
                {
                    "text": "チ",
                    "consonant": "ch",
                    "consonant_length": 0.05,
                    "vowel": "i",
                    "vowel_length": 0.06,
                },
                {
                    "text": "ワ",
                    "consonant": "w",
                    "consonant_length": 0.05,
                    "vowel": "a",
                    "vowel_length": 0.14,
                },
            ],
            "pause_mora": None,
        }
    ],
    "speedScale": 1.0,
    "prePhonemeLength": 0.1,
    "postPhonemeLength": 0.1,
}


#: AivisSpeech が実際に返すもの〔2026-08-15 実測〕。**モーラ長がすべて 0.0**。
AIVISSPEECH: dict[str, Any] = {
    "accent_phrases": [
        {
            "moras": [
                {
                    "text": "コ",
                    "consonant": "k",
                    "consonant_length": 0.0,
                    "vowel": "o",
                    "vowel_length": 0.0,
                },
                {
                    "text": "ン",
                    "consonant": None,
                    "consonant_length": None,
                    "vowel": "N",
                    "vowel_length": 0.0,
                },
                {
                    "text": "ニ",
                    "consonant": "n",
                    "consonant_length": 0.0,
                    "vowel": "i",
                    "vowel_length": 0.0,
                },
                {
                    "text": "チ",
                    "consonant": "ch",
                    "consonant_length": 0.0,
                    "vowel": "i",
                    "vowel_length": 0.0,
                },
                {
                    "text": "ワ",
                    "consonant": "w",
                    "consonant_length": 0.0,
                    "vowel": "a",
                    "vowel_length": 0.0,
                },
            ],
            "pause_mora": None,
        }
    ],
    "speedScale": 1.0,
    "prePhonemeLength": 0.1,
    "postPhonemeLength": 0.1,
}


class TestTimeline:
    def test_every_mora_becomes_a_span_in_order(self) -> None:
        timeline = build_timeline(KONNICHIWA)
        assert [span.viseme for span in timeline.spans] == [
            Viseme.O,
            None,  # 「ン」は口を閉じる
            Viseme.I,
            Viseme.I,
            Viseme.A,
        ]

    def test_spans_do_not_overlap_and_start_after_the_leading_silence(self) -> None:
        timeline = build_timeline(KONNICHIWA)
        assert timeline.spans[0].start_ms == 100  # prePhonemeLength
        for previous, current in zip(timeline.spans, timeline.spans[1:], strict=False):
            assert previous.start_ms + previous.duration_ms == current.start_ms

    def test_total_includes_the_silence_at_both_ends(self) -> None:
        timeline = build_timeline(KONNICHIWA)
        last = timeline.spans[-1]
        assert timeline.total_ms == last.start_ms + last.duration_ms + 100

    def test_speed_scale_shortens_everything(self) -> None:
        fast = build_timeline({**KONNICHIWA, "speedScale": 2.0})
        normal = build_timeline(KONNICHIWA)
        # **話速を反映しないと口だけずれる。**
        assert fast.total_ms == round(normal.total_ms / 2)

    def test_a_pause_between_phrases_closes_the_mouth(self) -> None:
        query = {
            **KONNICHIWA,
            "accent_phrases": [
                {
                    **KONNICHIWA["accent_phrases"][0],
                    "pause_mora": {"text": "、", "vowel": "pau", "vowel_length": 0.3},
                }
            ],
        }
        timeline = build_timeline(query)
        assert timeline.spans[-1].viseme is None
        assert timeline.spans[-1].duration_ms == 300


class TestEngineWithoutPhonemeLengths:
    """**AivisSpeech は音素長を返さない**〔2026-08-15 実測〕。

    それでも口は動かなければならない。モーラ列を音声の長さで割り振る
    → docs/interfaces/renderer.md「生成方式」
    """

    def test_spreads_the_moras_over_the_measured_audio(self) -> None:
        timeline = build_timeline(AIVISSPEECH, audio_seconds=1.22)
        assert len(timeline.spans) == 5
        assert timeline.total_ms == 1220
        # 先頭の無音のあとから始まり、隙間なく続く。
        assert timeline.spans[0].start_ms == 100
        for previous, current in zip(timeline.spans, timeline.spans[1:], strict=False):
            assert previous.start_ms + previous.duration_ms == current.start_ms

    def test_the_mouth_shapes_still_come_from_the_moras(self) -> None:
        # **振幅からの推定と違い、母音を取り違えない。**
        timeline = build_timeline(AIVISSPEECH, audio_seconds=1.22)
        assert [span.viseme for span in timeline.spans] == [
            Viseme.O,
            None,
            Viseme.I,
            Viseme.I,
            Viseme.A,
        ]

    def test_closed_moras_are_shorter_than_vowels(self) -> None:
        spans = build_timeline(AIVISSPEECH, audio_seconds=1.22).spans
        assert spans[1].duration_ms < spans[0].duration_ms

    def test_reported_lengths_win_when_the_engine_provides_them(self) -> None:
        # VOICEVOX は返す。**返ってきたらそちらを使う**（実際の発話と一致する）。
        timeline = build_timeline(KONNICHIWA, audio_seconds=99.0)
        assert timeline.total_ms != 99000

    def test_refuses_to_guess_without_any_duration(self) -> None:
        # 長さの根拠が無いのに口を動かすと、**音とずれたまま動き続ける**。
        assert build_timeline(AIVISSPEECH).spans == ()

    def test_refuses_when_the_audio_is_shorter_than_the_padding(self) -> None:
        assert build_timeline(AIVISSPEECH, audio_seconds=0.05).spans == ()


class TestBrokenInput:
    """**エンジンの出力を信用しない**（Invariant 3）。壊れていても落ちない。"""

    def test_an_unknown_vowel_closes_the_mouth(self) -> None:
        query = {
            "accent_phrases": [
                {"moras": [{"vowel": "xx", "vowel_length": 0.1, "consonant_length": None}]}
            ]
        }
        # 開けっ放しにしない（fail-closed）。
        assert build_timeline(query).spans[0].viseme is None

    def test_missing_fields_do_not_raise(self) -> None:
        assert build_timeline({}).spans == ()
        assert build_timeline({"accent_phrases": "nope"}).total_ms == 0
        assert build_timeline({"accent_phrases": [{"moras": [None, 3]}]}).spans == ()

    def test_absurd_lengths_are_treated_as_zero(self) -> None:
        query = {
            "accent_phrases": [
                {"moras": [{"vowel": "a", "vowel_length": -5.0, "consonant_length": 1e9}]}
            ]
        }
        assert build_timeline(query).total_ms == 0
