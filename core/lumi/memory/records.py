"""The shape of a memory. **Definition → docs/interfaces/memory.md.**

## Why an assertion mode exists at all

An LLM asked to extract memories from "冗談だけど、僕は火星人だよ" will happily produce
`user_species = "Martian", confidence = 0.95`. It is good at extracting *what was said*
and unreliable at judging *whether it was meant* (docs/architecture/memory.md §3).

So confidence and grounds are **separate axes**. "Strongly believed, but it is my own
guess" is a state that has to be representable — otherwise Lumi either presents its
guesses as facts or distrusts what the user plainly told it.

## The ordering is not a confidence ranking

`ASSERTION_STRENGTH` orders *grounds*, and it is what decides which of two contradicting
beliefs wins. A hunch held with confidence 0.9 still loses to something the user said with
confidence 0.5, because the question being asked is "who is in a position to know."
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from lumi.provenance import ProvenanceClass, TrustLevel


class MemoryType(StrEnum):
    """Which of the long-term layers a record belongs to (docs/architecture/memory.md §1)."""

    #: "On 2026-08-10 we talked about Factorio." Fades in weeks.
    EPISODIC = "episodic"
    #: "The user likes Factorio." A belief; fades in months.
    SEMANTIC = "semantic"
    #: A learned procedure. Barely fades.
    PROCEDURAL = "procedural"


class AssertionMode(StrEnum):
    """**On what grounds** Lumi holds this. Separate from how sure it is."""

    #: The user checked it in the memory UI. **The only escalation path to TRUSTED.**
    USER_CONFIRMED = "user_confirmed"
    #: The user said it outright.
    USER_STATED = "user_stated"
    #: Inferred from the conversation.
    INFERRED = "inferred"
    #: Lumi's own guess or imagination.
    SELF_GENERATED = "self_generated"
    #: From outside data.
    EXTERNAL = "external"


#: The ordering contradiction resolution compares. Ties go to the newer belief, so
#: "same strength" means "the user is allowed to change their mind."
ASSERTION_STRENGTH: Final[Mapping[AssertionMode, int]] = {
    AssertionMode.USER_CONFIRMED: 4,
    AssertionMode.USER_STATED: 3,
    AssertionMode.INFERRED: 2,
    AssertionMode.SELF_GENERATED: 1,
    AssertionMode.EXTERNAL: 0,
}

#: Retrieval multiplier (docs/architecture/memory.md §7). **Multiplied, never added**:
#: as an addend, a memory with similarity 0 would still surface on its grounds alone.
ASSERTION_WEIGHT: Final[Mapping[AssertionMode, float]] = {
    AssertionMode.USER_CONFIRMED: 1.2,
    AssertionMode.USER_STATED: 1.0,
    AssertionMode.INFERRED: 0.8,
    AssertionMode.SELF_GENERATED: 0.6,
    AssertionMode.EXTERNAL: 0.7,
}


def at_least_as_strong(new: AssertionMode, existing: AssertionMode) -> bool:
    """Whether `new` may supersede `existing`. **Equal counts as strong enough.**"""
    return ASSERTION_STRENGTH[new] >= ASSERTION_STRENGTH[existing]


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A proposed memory, **before anything has agreed to believe it.**

    What a Reflection Job produces. The store checks it rather than taking its word:
    the evidence has to exist, `user_confirmed` is refused here, and the trust it
    declares is joined with the trust of the utterances it names.
    """

    type: MemoryType
    subject: str
    content: str
    assertion_mode: AssertionMode

    #: **No default.** A candidate that forgot to say where it came from would otherwise
    #: arrive trusted, which is an escalation nobody wrote down — and the caller is the
    #: only one who knows what the extraction was made from.
    provenance_class: ProvenanceClass
    trust_level: TrustLevel

    #: Utterance ids. **Checked for existence at write time**, then allowed to dangle:
    #: the episode expires long before the belief does.
    evidence_ref: tuple[str, ...] = ()
    confidence: float = 0.5
    base_salience: float = 0.5
    valid_from: datetime | None = None
    source_episode_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """A memory as it is on disk.

    **Frozen**, like every record that carries provenance: a mutable one is a record
    whose trust can be edited after a Policy decision has already read it.

    `superseded_by` being set does not mean this row was replaced in place — the newer
    belief is a different row, and this one is still readable, which is what lets Lumi
    say "前は Factorio 好きって言ってたよね" (docs/architecture/memory.md §6).
    """

    id: str
    type: MemoryType
    subject: str
    content: str

    assertion_mode: AssertionMode
    evidence_ref: tuple[str, ...]
    confidence: float

    provenance_class: ProvenanceClass
    trust_level: TrustLevel

    base_salience: float
    created_at: datetime
    last_accessed: datetime
    access_count: int
    archived_at: datetime | None

    valid_from: datetime
    superseded_by: str | None

    source_episode_ids: tuple[str, ...] = ()
    #: Which embedding produced this record's vector. **Empty until 2e**; a mismatch is
    #: how re-embedding after a model change is detected rather than guessed at.
    embedding_model_id: str = ""

    @property
    def is_live(self) -> bool:
        """Not superseded and not archived. **What ordinary retrieval may see.**"""
        return self.superseded_by is None and self.archived_at is None
