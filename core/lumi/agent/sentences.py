"""文分割 — LLM のトークンストリームを TTS の単位に切る。

設計 → docs/architecture/audio.md §6

## なぜ文単位なのか

**最初の音が出るまでの時間が、体感のほぼすべて**（SLO p50 < 1.2 s）。
全文が揃うのを待つと、長い返事ほど待たされる。第1文だけ先に喋り始める。

## 切りすぎない

短く切るほど早く喋り出せるが、**細切れの音声はイントネーションが壊れる**。
句読点の「。」で切り、「、」では切らない。長すぎる場合だけ「、」を使う。

## 終端が来たら待たない

`やった！` の直後に `」` が来るかもしれない — が、**それを待つと第1文が必ず1チャンク分遅れる**。
遅れるのは「最初の音が出るまで」であり、そこが体感のほぼすべてである。
待たずに切り、はぐれた閉じ記号は `is_speakable` が落とす。**読み上げないものを待たない。**
"""

from __future__ import annotations

from typing import Final

#: ここで文が終わる
TERMINATORS: Final = frozenset("。！？!?\n")
#: 終端のあとに続きうる閉じ記号。**これを含めてから切る**（「そうだね！」の ！ を落とさない）
CLOSERS: Final = frozenset("」』）)】〕》”\"'…♪〜~ 　")
#: 終端が来ないまま伸びたら、ここで諦めて切る〔Provisional〕
MAX_CHARS: Final = 60
#: 諦めるときに優先して使う区切り
SOFT_BREAKS: Final = frozenset("、,・：:；;")

#: これだけしか無い断片は TTS に投げない（読み上げるものが無い）
_UNSPEAKABLE: Final = frozenset("。！？!?、,・：:；;「」『』（）()【】〕《》\"'…♪〜~\n\r\t 　")


class SentenceStream:
    """ストリーミング分割。**確定した文だけ返す。**"""

    __slots__ = ("_buffer",)

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        sentences: list[str] = []

        while True:
            cut = self._find_cut()
            if cut is None:
                break
            head, self._buffer = self._buffer[:cut], self._buffer[cut:]
            if is_speakable(head):
                sentences.append(head.strip())

        return sentences

    def flush(self) -> list[str]:
        """ストリームが終わった。**残りを吐く**（終端が無くても喋る）。"""
        remainder, self._buffer = self._buffer, ""
        return [remainder.strip()] if is_speakable(remainder) else []

    def _find_cut(self) -> int | None:
        for index, char in enumerate(self._buffer):
            if char in TERMINATORS:
                end = index + 1
                while end < len(self._buffer) and self._buffer[end] in CLOSERS:
                    end += 1
                return end

        if len(self._buffer) < MAX_CHARS:
            return None

        # 終端が来ない。**諦めるが、切る場所は選ぶ**
        window = self._buffer[:MAX_CHARS]
        for index in range(len(window) - 1, 0, -1):
            if window[index] in SOFT_BREAKS:
                return index + 1
        return MAX_CHARS


def is_speakable(text: str) -> bool:
    """読み上げる中身があるか。**記号と空白だけの断片を TTS に投げない。**"""
    return any(char not in _UNSPEAKABLE for char in text)
