"""「覚えておいて」. **What the user says when they want something kept.**

Design → docs/architecture/memory.md §4

Two callers, and they are on opposite sides of the system: a conversation turn notices the
phrase as it goes past (`agent/reactive.py`), and reflection asks afterwards whether the
transcript contained one (`memory/extraction.py`). **The list has to be the same list** —
what triggers a reflection pass and what counts as important within it cannot disagree, or
Lumi reflects because it was asked and then files the result as unremarkable.

It lived in the extractor, which meant a conversation turn imported the memory-extraction
module to find out whether the user had just said something. That is backwards: noticing
the phrase is not extraction, and it happens minutes earlier.

## Why a substring match and not a classifier

Getting this wrong in the permissive direction costs one early reflection pass. Getting it
wrong the other way means 「覚えておいて」 was heard and quietly ignored — **which is the
failure people remember**. "Did they mean it as important" is exactly the judgement an LLM
is unreliable at; "did they say 覚えておいて" is not a judgement at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from lumi.storage.memory import SPEAKER_USER, Utterance

#: Phrases that mean "keep this". **Counted, not judged** — the deterministic half of
#: salience (docs/architecture/memory.md §4). Japanese first because that is what Lumi is
#: spoken to in; the English forms cost nothing to include.
REMEMBER_PHRASES: Final = (
    "覚えておいて",
    "覚えといて",
    "忘れないで",
    "記憶しておいて",
    "remember this",
    "don't forget",
)


def asked_to_remember(text: str) -> bool:
    """Whether an utterance is the user asking for something to be remembered."""
    # **Case-folded for the English phrases.** "Remember this" at the start of a sentence
    # is the ordinary way to write it, and a trigger that misses the capitalised form is
    # one that misses it exactly when someone is being deliberate. The Japanese phrases
    # are unaffected — `casefold` leaves them as they are.
    folded = text.casefold()
    return any(phrase.casefold() in folded for phrase in REMEMBER_PHRASES)


def explicit_marking(lines: Sequence[Utterance]) -> bool:
    """Whether the user asked, in so many words, for this to be kept.

    **Only the user's own lines count.** Lumi repeating 「覚えておいて」 back is not a
    request, and reading it as one would let a reply raise the salience of its own subject.
    """
    return any(asked_to_remember(line.text) for line in lines if line.speaker == SPEAKER_USER)
