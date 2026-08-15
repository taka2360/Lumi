"""リップシンクのタイムライン。**純粋関数**（HTTP もファイルも触らない）。

設計 → docs/interfaces/renderer.md「生成方式」

`audio_query` が返すモーラ列から、いつどの口の形にするかを組み立てる。

**エンジンが音素長を返すとは限らない**〔2026-08-15 実測〕。
AivisSpeech は `adjust_phoneme_length: false` であり、`audio_query` のモーラ長は
**すべて 0.0** で返る（長さはモデルが合成時に決めるため）。VOICEVOX は返す。

そこで:

| エンジンが返すもの | 使うもの |
|---|---|
| 音素長がある（VOICEVOX） | **その値**。実際の発話と一致する |
| 音素長が無い（AivisSpeech） | **モーラ列 + 合成された音声の長さ**から等分する |

どちらの場合も**口の形はモーラ列から決まる**。振幅からの推定と違い、母音を取り違えない。

型の置き場所: Phase 0 では TTS の隣に置く。Phase 1 で `ExpressionIntent` /
`MotionIntent` を作るときに、Renderer への意図としてまとめて移す。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Viseme(StrEnum):
    """VRM の標準ビセーム（`aa` / `ih` / `ou` / `ee` / `oh`）に対応する。

    **ここが唯一の定義場所。** Renderer ごとに再定義しない
    （AIRI は `Emotion` を4箇所に重複定義して壊れている）。
    """

    A = "A"
    # 母音の名前なので `I` から変えない（`I_` にすると Stage 側の値と食い違う）。
    I = "I"  # noqa: E741
    U = "U"
    E = "E"
    O = "O"  # noqa: E741


#: `audio_query` の母音表記 → ビセーム。
#: 大文字は無声化母音（VOICEVOX 系の表記）。**口の形は同じ**なので同じものに送る。
_VOWEL_TO_VISEME: Mapping[str, Viseme] = {
    "a": Viseme.A,
    "i": Viseme.I,
    "u": Viseme.U,
    "e": Viseme.E,
    "o": Viseme.O,
    "A": Viseme.A,
    "I": Viseme.I,
    "U": Viseme.U,
    "E": Viseme.E,
    "O": Viseme.O,
}

#: 表に無いもの（撥音「ん」・促音「っ」・無音 `pau`、そして**知らない表記**）は
#: すべて「口を閉じる」に落ちる。開けっ放しにしない（fail-closed）。


@dataclass(frozen=True, slots=True)
class VisemeSpan:
    """1つの口の形を、いつからいつまで出すか。

    `viseme` が `None` なら口を閉じる。
    """

    viseme: Viseme | None
    start_ms: int
    duration_ms: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "viseme": str(self.viseme) if self.viseme else None,
            "start_ms": self.start_ms,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class VisemeTimeline:
    spans: tuple[VisemeSpan, ...]
    total_ms: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "spans": [span.to_payload() for span in self.spans],
            "total_ms": self.total_ms,
        }


def _seconds(value: Any) -> float:
    """壊れた値を 0 として扱う。**エンジンの出力は信用しない**（Invariant 3）。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if result > 0.0 and result < 3600.0 else 0.0
    return 0.0


def _moras_of(phrase: Any) -> Sequence[Any]:
    if not isinstance(phrase, Mapping):
        return ()
    moras = phrase.get("moras")
    return moras if isinstance(moras, Sequence) and not isinstance(moras, (str, bytes)) else ()


#: 口を閉じるモーラ（撥音・促音・無音）の相対的な長さ。
#: エンジンが音素長を返さないときの等分に使う。**「ん」は母音より短い。**
_CLOSED_WEIGHT = 0.7


@dataclass(frozen=True, slots=True)
class _Mora:
    """モーラ1つ。`length` はエンジンが返した長さ（秒）。0 なら「不明」。"""

    viseme: Viseme | None
    length: float


def _collect(query: Mapping[str, Any]) -> list[_Mora]:
    """モーラ列だけを取り出す。**時間の割り当てはしない。**"""
    phrases = query.get("accent_phrases")
    if not isinstance(phrases, Sequence) or isinstance(phrases, (str, bytes)):
        return []

    moras: list[_Mora] = []
    for phrase in phrases:
        for mora in _moras_of(phrase):
            if not isinstance(mora, Mapping):
                continue
            # 子音は母音への移行区間。**母音と一緒に1つの span にする**ことで、
            # 口が母音より少し早く開く（実際の口の動きに近い）。
            length = _seconds(mora.get("consonant_length")) + _seconds(mora.get("vowel_length"))
            vowel = mora.get("vowel")
            viseme = _VOWEL_TO_VISEME.get(vowel) if isinstance(vowel, str) else None
            moras.append(_Mora(viseme=viseme, length=length))

        pause = phrase.get("pause_mora") if isinstance(phrase, Mapping) else None
        if isinstance(pause, Mapping):
            moras.append(_Mora(viseme=None, length=_seconds(pause.get("vowel_length"))))
    return moras


def build_timeline(query: Mapping[str, Any], audio_seconds: float | None = None) -> VisemeTimeline:
    """`audio_query` の応答（と、合成された音声の長さ）から口のタイムラインを作る。

    **速度倍率を必ず反映する。** 反映しないと、話速を変えた瞬間に口だけずれる。

    エンジンが音素長を返さない場合は `audio_seconds` を等分する。
    **`audio_seconds` も無ければ、何も返さない**（でたらめな時間で口を動かさない）。
    """
    speed = _seconds(query.get("speedScale")) or 1.0
    pre = _seconds(query.get("prePhonemeLength")) / speed
    post = _seconds(query.get("postPhonemeLength")) / speed

    moras = _collect(query)
    if not moras:
        return VisemeTimeline(spans=(), total_ms=0)

    reported = sum(mora.length for mora in moras)
    if reported > 0.0:
        durations = [mora.length / speed for mora in moras]
    elif audio_seconds is not None and audio_seconds > pre + post:
        # エンジンが長さを返さない（AivisSpeech）。**実際の音声の長さで割り振る。**
        weights = [_CLOSED_WEIGHT if mora.viseme is None else 1.0 for mora in moras]
        total_weight = sum(weights)
        speech = audio_seconds - pre - post
        durations = [speech * weight / total_weight for weight in weights]
    else:
        # 長さの根拠が無い。**適当な時間で口を動かさない。**
        return VisemeTimeline(spans=(), total_ms=0)

    spans: list[VisemeSpan] = []
    elapsed = pre
    for mora, duration in zip(moras, durations, strict=True):
        spans.append(
            VisemeSpan(
                viseme=mora.viseme,
                start_ms=round(elapsed * 1000),
                duration_ms=round(duration * 1000),
            )
        )
        elapsed += duration

    return VisemeTimeline(spans=tuple(spans), total_ms=round((elapsed + post) * 1000))
