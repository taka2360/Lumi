"""`<|ACT {...}|>` — an expression directive embedded in the LLM's token stream.

Design → docs/architecture/agent.md §3 "Inline markers" (borrows AIRI's approach)

## Rules to keep

| | |
|---|---|
| **Strip before speaking** | Reading "pipe ACT brace" out loud would ruin it |
| **Drop the whole marker on parse failure** | Don't speak it. **Never read it half-parsed** |
| **Must not break if the stream cuts mid-marker** | Markers spanning chunk boundaries is normal |

## Handling an "unknown emotion"

renderer.md's "the Renderer falls back for an unknown emotion" refers to the case where
**the emotion exists in `Emotion` but the model has no way to express it**.
A string not in `Emotion` is dropped here as a parse failure — don't put something onto
the wire that isn't in the type.
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
    """Result of a single `feed` call."""

    #: **Text that can be passed straight to TTS.** Markers already stripped
    text: str
    intents: tuple[ExpressionIntent, ...]


class MarkerStream:
    """Streaming parser. **Holds onto incomplete markers.**

    Without this, `<|ACT {"emo` would get spoken as-is the moment it arrives.
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
                # Hold back a tail that **might be the start of a marker**
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
                # Not closed yet. **Wait for more to arrive**
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
        """The stream has ended. **Discard any unterminated marker.**

        Reading it aloud on the assumption "it's probably just text" always produces
        something weird when that assumption is wrong.
        """
        remainder = self._buffer
        self._buffer = ""
        if remainder.startswith(MARKER_OPEN) or _is_open_prefix(remainder):
            log.info("marker.unterminated", dropped=remainder[:40])
            return ""
        return remainder


def parse_marker(body: str) -> ExpressionIntent | None:
    """Read the content between `<|ACT` and `|>`. **Returns None on failure** (drops the whole
    marker).
    """
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
        # **Don't put something onto the wire that isn't in the type.** The Renderer's fallback is
        # not what this is about
        log.info("marker.unknown_emotion", emotion=str(raw_emotion)[:40])
        return None

    return ExpressionIntent(
        emotion=Emotion(raw_emotion),
        intensity=_clamp(payload.get("intensity"), default=0.7),
        blend_ms=_positive_int(payload.get("blend_ms")) or 200,
        duration_ms=_positive_int(payload.get("duration_ms")),
    )


def _clamp(value: Any, *, default: float) -> float:
    """**Clamp out-of-range values instead of discarding them.** No reason to lose an expression
    just because the intensity is off.
    """
    if not isinstance(value, int | float) or isinstance(value, bool):
        return default
    return max(0.0, min(1.0, float(value)))


def _positive_int(value: Any) -> int | None:
    """**Treat a malformed value like "not specified"** (don't drop the whole marker for it)."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value


def _partial_open_length(text: str) -> int:
    """Length of the trailing partial match of `<|ACT`. 0 if none."""
    for length in range(min(len(MARKER_OPEN) - 1, len(text)), 0, -1):
        if text.endswith(MARKER_OPEN[:length]):
            return length
    return 0


def _is_open_prefix(text: str) -> bool:
    return bool(text) and MARKER_OPEN.startswith(text)
