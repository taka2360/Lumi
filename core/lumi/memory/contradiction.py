"""What happens when the new belief disagrees with the old one.

Design → docs/architecture/memory.md §6

## The old row is never edited

"The user likes Factorio" does not become "the user plays Rimworld"; a second row is
written and the first points at it. That is what lets Lumi say **"前は Factorio 好きって
言ってたけど、最近はどう?"** — a conversation with a past in it, rather than a system that
only ever knows the current value.

## Deciding is code's job, not the LLM's

The comparison is a pure function of two records' `assertion_mode`. An LLM asked "which of
these should win" answers differently on Tuesday, and the answer decides what Lumi will
believe about the user for months.

## Equal strength lets the newer one win

**People change their minds**, and two `user_stated` beliefs a year apart are usually not a
contradiction to adjudicate but a life that moved on. Only a *weaker* new claim is held
back — a guess does not get to overwrite something the user said.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from lumi.memory.records import MemoryCandidate, MemoryRecord, at_least_as_strong


class Resolution(StrEnum):
    """What to do with a candidate that collides with an existing belief.

    `NEW` is the one value `resolve()` never returns: **nothing collided**, so there was
    nothing to resolve. It exists so that the caller reporting what happened does not have
    to describe a first belief as though it had won an argument.
    """

    #: No existing belief about this subject stood in the way.
    NEW = "new"
    #: Same subject, same content. Nothing new is believed; the existing one is reinforced.
    DUPLICATE = "duplicate"
    #: The candidate is at least as well grounded. The existing belief gets a successor.
    SUPERSEDE = "supersede"
    #: The candidate is more weakly grounded. **Kept, with its confidence cut** — not
    #: discarded, because a guess that turns out right should still be recoverable, and
    #: not promoted, because a guess does not outrank what the user said.
    KEEP_WEAK = "keep_weak"


#: What a losing candidate's confidence is multiplied by. **Provisional.**
WEAK_CONFIDENCE_FACTOR: Final = 0.5


def normalize(content: str) -> str:
    """For the duplicate check. **Whitespace only** — this is not semantic comparison.

    Deciding that two differently worded sentences mean the same thing needs embeddings
    (2e). Until then the honest position is that Lumi cannot tell, not that it checked.
    """
    return " ".join(content.split())


def resolve(existing: MemoryRecord, candidate: MemoryCandidate) -> Resolution:
    """Which of the two survives as the live belief about this subject."""
    if normalize(existing.content) == normalize(candidate.content):
        return Resolution.DUPLICATE
    if at_least_as_strong(candidate.assertion_mode, existing.assertion_mode):
        return Resolution.SUPERSEDE
    return Resolution.KEEP_WEAK


def contradiction_note(existing: MemoryRecord, new_content: str) -> str:
    """The line written into episodic memory when a belief is replaced.

    **In Japanese, and phrased as something Lumi noticed**, because it exists to be read
    back into a prompt rather than into a log. Without it, superseding is indistinguishable
    from a silent overwrite: the new belief is there, and nothing remembers that it changed.
    """
    return (
        f"{existing.subject} について、以前は「{existing.content}」だったが、"
        f"いまは「{new_content}」。"
    )
