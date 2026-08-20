"""Lip-sync timeline. **Pure functions** (touches neither HTTP nor files).

Design → docs/interfaces/renderer.md "Generation method"

Builds when to show which mouth shape from the mora sequence `audio_query` returns.

**The engine doesn't always return phoneme lengths** [observed 2026-08-15].
AivisSpeech has `adjust_phoneme_length: false`, and `audio_query`'s mora lengths come
back as **all 0.0** (the model decides the length during synthesis). VOICEVOX does
return them.

So:

| What the engine returns | What's used |
|---|---|
| Phoneme lengths present (VOICEVOX) | **Those values.** Matches the actual utterance |
| No phoneme lengths (AivisSpeech) | **Divides evenly** across the mora sequence + audio length |

Either way, **the mouth shape is decided from the mora sequence.** Unlike an amplitude-
based estimate, this never mistakes one vowel for another.

Where the type lives: in Phase 0 it sits next to TTS. When `ExpressionIntent` /
`MotionIntent` are built in Phase 1, it moves together as intent sent to the Renderer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Viseme(StrEnum):
    """Corresponds to VRM's standard visemes (`aa` / `ih` / `ou` / `ee` / `oh`).

    **This is the single source of definition.** Never redefine per Renderer
    (AIRI is broken from defining `Emotion` redundantly in 4 places).
    """

    A = "A"
    # This is a vowel name, so it stays `I` (renaming to `I_` would mismatch the Stage-side value).
    I = "I"  # noqa: E741
    U = "U"
    E = "E"
    O = "O"  # noqa: E741


# : `audio_query`'s vowel notation → viseme. : Uppercase is a devoiced vowel (VOICEVOX-family
# notation). **The mouth shape is the same**, so it maps to the same value.
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

#: Anything not in the table (the moraic nasal "ん", the geminate "っ", silence `pau`,
#: and **any unrecognized notation**) all fall through to "close the mouth." Never
#: leaves it hanging open (fail-closed).


@dataclass(frozen=True, slots=True)
class VisemeSpan:
    """When to show one mouth shape, from when to when.

    If `viseme` is `None`, the mouth is closed.
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
    """Treats malformed values as 0. **Never trust the engine's output** (Invariant 3)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if result > 0.0 and result < 3600.0 else 0.0
    return 0.0


def _moras_of(phrase: Any) -> Sequence[Any]:
    if not isinstance(phrase, Mapping):
        return ()
    moras = phrase.get("moras")
    return moras if isinstance(moras, Sequence) and not isinstance(moras, (str, bytes)) else ()


#: Relative length of mora that close the mouth (moraic nasal / geminate / silence).
#: Used when dividing time evenly if the engine doesn't return phoneme lengths.
#: **"ん" is shorter than a vowel.**
_CLOSED_WEIGHT = 0.7


@dataclass(frozen=True, slots=True)
class _Mora:
    """One mora. `length` is the engine-reported length (seconds). 0 means "unknown."""

    viseme: Viseme | None
    length: float


def _collect(query: Mapping[str, Any]) -> list[_Mora]:
    """Extracts just the mora sequence. **Doesn't assign timing.**"""
    phrases = query.get("accent_phrases")
    if not isinstance(phrases, Sequence) or isinstance(phrases, (str, bytes)):
        return []

    moras: list[_Mora] = []
    for phrase in phrases:
        for mora in _moras_of(phrase):
            if not isinstance(mora, Mapping):
                continue
            # The consonant is the transition into the vowel. **Merging it into one
            # span with the vowel** makes the mouth open slightly before the vowel
            # (closer to how mouths actually move).
            length = _seconds(mora.get("consonant_length")) + _seconds(mora.get("vowel_length"))
            vowel = mora.get("vowel")
            viseme = _VOWEL_TO_VISEME.get(vowel) if isinstance(vowel, str) else None
            moras.append(_Mora(viseme=viseme, length=length))

        pause = phrase.get("pause_mora") if isinstance(phrase, Mapping) else None
        if isinstance(pause, Mapping):
            moras.append(_Mora(viseme=None, length=_seconds(pause.get("vowel_length"))))
    return moras


def build_timeline(query: Mapping[str, Any], audio_seconds: float | None = None) -> VisemeTimeline:
    """Builds the mouth timeline from `audio_query`'s response (and the synthesized
    audio's length).

    **Always accounts for the speed multiplier.** Skipping it makes the mouth drift
    the instant speech speed changes.

    If the engine doesn't return phoneme lengths, `audio_seconds` is divided evenly.
    **If `audio_seconds` is also missing, nothing is returned** (never move the mouth
    on bogus timing).
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
        # The engine doesn't return lengths (AivisSpeech). **Allocated using the actual audio
        # length.**
        weights = [_CLOSED_WEIGHT if mora.viseme is None else 1.0 for mora in moras]
        total_weight = sum(weights)
        speech = audio_seconds - pre - post
        durations = [speech * weight / total_weight for weight in weights]
    else:
        # No basis for timing. **Never move the mouth on made-up timing.**
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
