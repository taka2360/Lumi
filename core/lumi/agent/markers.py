"""`<|ACT {...}|>` — LLM ストリームに埋め込まれた表情指示。

設計 → docs/architecture/agent.md §3「インラインマーカー」（AIRI のアプローチを借用）

## 守ること

| | |
|---|---|
| **音声化前に除去する** | 「縦棒 ACT 波括弧」と読み上げたら台無しになる |
| **パース失敗はマーカーごと落とす** | 読み上げない。**中途半端に読ませない** |
| **ストリームの途中で切れても壊れない** | マーカーがチャンク境界を跨ぐのは普通に起きる |

## 「未知の emotion」の扱い

renderer.md の「未知の emotion は Renderer 側でフォールバック」は、
**`Emotion` にはあるが、そのモデルに表現手段が無い**場合の話である。
`Emotion` に無い文字列はここでパース失敗として落とす — 型に無いものを線に乗せない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from lumi import logging as lumi_logging
from lumi.character import Emotion, ExpressionIntent

log = lumi_logging.get_logger(__name__)

MARKER_OPEN: Final = "<|ACT"
MARKER_CLOSE: Final = "|>"


@dataclass(frozen=True, slots=True)
class MarkerChunk:
    """1回の `feed` の結果。"""

    #: **そのまま TTS に渡してよいテキスト。** マーカーは除去済み
    text: str
    intents: tuple[ExpressionIntent, ...]


class MarkerStream:
    """ストリーミングパーサ。**未完のマーカーは手元に留める。**

    留めないと、`<|ACT {"emo` までしか来ていない時点でそれを喋ってしまう。
    """

    __slots__ = ("_buffer",)

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> MarkerChunk:
        self._buffer += chunk
        text: list[str] = []
        intents: list[ExpressionIntent] = []

        while True:
            start = self._buffer.find(MARKER_OPEN)
            if start < 0:
                # マーカーの**先頭になりかけている**末尾は残す
                hold = _partial_open_length(self._buffer)
                if hold:
                    text.append(self._buffer[:-hold])
                    self._buffer = self._buffer[-hold:]
                else:
                    text.append(self._buffer)
                    self._buffer = ""
                break

            end = self._buffer.find(MARKER_CLOSE, start + len(MARKER_OPEN))
            if end < 0:
                # 閉じていない。**続きが来るまで待つ**
                text.append(self._buffer[:start])
                self._buffer = self._buffer[start:]
                break

            text.append(self._buffer[:start])
            intent = parse_marker(self._buffer[start + len(MARKER_OPEN) : end])
            if intent is not None:
                intents.append(intent)
            self._buffer = self._buffer[end + len(MARKER_CLOSE) :]

        return MarkerChunk(text="".join(text), intents=tuple(intents))

    def flush(self) -> str:
        """ストリームが終わった。**閉じていないマーカーは捨てる。**

        「たぶんテキストだろう」で読み上げると、失敗したときに必ず変な音が出る。
        """
        remainder = self._buffer
        self._buffer = ""
        if remainder.startswith(MARKER_OPEN) or _is_open_prefix(remainder):
            log.info("marker.unterminated", dropped=remainder[:40])
            return ""
        return remainder


def parse_marker(body: str) -> ExpressionIntent | None:
    """`<|ACT` と `|>` の間を読む。**失敗したら None**（マーカーごと落ちる）。"""
    try:
        payload: Any = json.loads(body.strip())
    except json.JSONDecodeError:
        log.info("marker.invalid_json", body=body[:60])
        return None

    if not isinstance(payload, dict):
        log.info("marker.not_an_object", body=body[:60])
        return None

    raw_emotion = payload.get("emotion")
    if raw_emotion not in tuple(Emotion):
        # **型に無いものを線に乗せない。** Renderer のフォールバックはここの話ではない
        log.info("marker.unknown_emotion", emotion=str(raw_emotion)[:40])
        return None

    return ExpressionIntent(
        emotion=Emotion(raw_emotion),
        intensity=_clamp(payload.get("intensity"), default=0.7),
        blend_ms=_positive_int(payload.get("blend_ms")) or 200,
        duration_ms=_positive_int(payload.get("duration_ms")),
    )


def _clamp(value: Any, *, default: float) -> float:
    """**範囲外は捨てずに丸める。** 強度がおかしいだけで表情を失う理由が無い。"""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return default
    return max(0.0, min(1.0, float(value)))


def _positive_int(value: Any) -> int | None:
    """**壊れた値は「指定が無かった」と同じ扱いにする**（マーカーごとは落とさない）。"""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value


def _partial_open_length(text: str) -> int:
    """末尾が `<|ACT` の途中になっている長さ。無ければ 0。"""
    for length in range(min(len(MARKER_OPEN) - 1, len(text)), 0, -1):
        if text.endswith(MARKER_OPEN[:length]):
            return length
    return 0


def _is_open_prefix(text: str) -> bool:
    return bool(text) and MARKER_OPEN.startswith(text)
