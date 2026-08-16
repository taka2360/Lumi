"""Sentence splitting — cuts the LLM's token stream into TTS-sized units.

Design → docs/architecture/audio.md §6

## Why sentence-sized units

**Time to first sound is almost the entire perceived experience** (SLO p50 < 1.2 s).
Waiting for the full response means longer replies wait longer. Start speaking the first
sentence as soon as it's ready.

## Don't cut too aggressively

Shorter cuts start speech sooner, but **choppy audio breaks intonation**.
Cut on the punctuation mark "。", not "、". Only fall back to "、" when a segment is too long.

## Don't wait for a terminator

A `」` might follow right after `やった！` — but **waiting for it always delays the first
sentence by a full chunk**. The delay hits "time to first sound," which is almost the entire
perceived experience. Cut without waiting; stray closing marks get dropped by `is_speakable`.
**Don't wait for something that won't be spoken.**
"""

from __future__ import annotations

from typing import Final

#: A sentence ends here
TERMINATORS: Final = frozenset("。！？!?\n")
#: Closing marks that may follow a terminator. **Include these before cutting** (don't drop the ！ in "そうだね！")
CLOSERS: Final = frozenset("」』）)】〕》”\"'…♪〜~ 　")
#: If nothing terminates before this length, give up and cut here [Provisional]
MAX_CHARS: Final = 60
#: Preferred break points to use when giving up
SOFT_BREAKS: Final = frozenset("、,・：:；;")

#: Fragments consisting only of these are never sent to TTS (nothing to speak)
_UNSPEAKABLE: Final = frozenset("。！？!?、,・：:；;「」『』（）()【】〕《》\"'…♪〜~\n\r\t 　")


class SentenceStream:
    """Streaming split. **Only returns confirmed sentences.**"""

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
        """The stream has ended. **Flush the remainder** (speak it even without a terminator)."""
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

        # No terminator arrived. **Give up, but still choose where to cut**
        window = self._buffer[:MAX_CHARS]
        for index in range(len(window) - 1, 0, -1):
            if window[index] in SOFT_BREAKS:
                return index + 1
        return MAX_CHARS


def is_speakable(text: str) -> bool:
    """Whether there's anything to speak. **Never send symbol-and-whitespace-only fragments to TTS.**"""
    return any(char not in _UNSPEAKABLE for char in text)
