"""キャラクターに渡す「意図」。**パラメータではない。**

型の定義 → docs/interfaces/renderer.md / 決定 → ADR-009

VRM と Live2D は表情モデルが根本的に異なる
（名前付きブレンドシェイプの合成 / 生パラメータの直接操作）。
**Core がブレンドシェイプ名を知っていたら、Live2D では意味をなさない。**

> **Renderer は合成に関与しない。** 受け取った意図をそのまま表現し、
> 表現できなければ**自分で**フォールバックする。Core は `capabilities()` を見て分岐しない。

**`Emotion` はここにだけ定義する**（docs/interfaces/renderer.md テスト1）。
Renderer ごとに再定義しない。線に乗る値なので `docs/contracts/wire.json` とも突き合わせる。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Emotion(StrEnum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    THINK = "think"
    CURIOUS = "curious"
    AWKWARD = "awkward"
    SLEEPY = "sleepy"


@dataclass(frozen=True, slots=True)
class ExpressionIntent:
    """`intensity` は 0.0-1.0 で伝える。

    **実際の値域へのマッピングは Renderer 側の実装詳細。** VRM のブレンドシェイプは
    1.0 まで指定できるが、0.7-0.8 程度に抑えたほうが自然に見える。
    それは表現の詳細であって、Core が持つ知識ではない。
    """

    emotion: Emotion
    intensity: float = 0.7
    blend_ms: int = 200
    #: None なら維持。指定があれば自動で戻る
    duration_ms: int | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "emotion": self.emotion.value,
            "intensity": self.intensity,
            "blend_ms": self.blend_ms,
            "duration_ms": self.duration_ms,
        }
