"""Forgetting, and how important something was in the first place.

Design → docs/architecture/memory.md §4 (salience) and §5 (decay)

**Everything here is a pure function of its arguments**, including the clock. A decay rule
that can only be observed by leaving the program running for six weeks is one that ships
untested, and the numbers below are the ones that decide what Lumi still knows about you
next month.

## Two different things are called salience

| | What it is | When it is computed |
|---|---|---|
| `base_salience` | How much this mattered when it was formed | Once, at write time |
| `effective_salience` | What it is worth *now* | Every retrieval, never stored |

Storing the effective value would freeze the decay at whatever moment the row was last
written, which is precisely the thing that must not depend on when Lumi happened to run.

## Why the correction is not just the LLM's number

Asked how important something was, an LLM answers plausibly and inconsistently — the same
utterance scores 0.8 on one run and 0.4 on the next. It contributes 40%; the rest comes
from things that are counted rather than judged (docs/architecture/memory.md §4).

**Out-of-range inputs are rounded, not rejected.** A model that returns 1.4 for
`emotional_intensity` has still told us "very"; dropping the candidate would lose a memory
over a formatting slip. `NaN` is different — it is not a number, and clamping it would
quietly turn it into one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from lumi.memory.records import MemoryRecord, MemoryType

#: How fast each layer fades (docs/architecture/memory.md §5). **Provisional** — the table
#: in the design doc is the definition, and a change belongs there in the same commit.
TAU: Final[Mapping[MemoryType, timedelta]] = {
    MemoryType.EPISODIC: timedelta(days=14),
    MemoryType.SEMANTIC: timedelta(days=180),
    MemoryType.PROCEDURAL: timedelta(days=730),
}

#: Below this a memory is archived: it stops turning up in ordinary retrieval. **It is not
#: deleted** — archiving is "cannot recall", not "never happened" (§5).
FLOOR: Final = 0.05

#: The most that being recalled can add back. Capped, because otherwise anything retrieved
#: often enough becomes permanent regardless of how little it mattered — and what gets
#: retrieved often is partly a function of what retrieval already favours.
ACCESS_BOOST_CAP: Final = 0.2
#: Recalls needed for the boost to reach ~63% of its cap.
ACCESS_BOOST_SCALE: Final = 5.0

#: Weights of the deterministic correction (docs/architecture/memory.md §4).
WEIGHT_LLM: Final = 0.4
WEIGHT_EMOTION: Final = 0.2
WEIGHT_NOVELTY: Final = 0.15
WEIGHT_EXPLICIT: Final = 0.15
WEIGHT_REPETITION: Final = 0.10

#: Where repetition stops counting. Saying something five times and fifty times both mean
#: "this matters"; without a cap the term would flatten every other one.
REPETITION_CAP: Final = 5


def clamp01(value: float, *, name: str) -> float:
    """Into [0, 1]. **`NaN` raises** — it is not a value that got rounded off."""
    if math.isnan(value):
        raise ValueError(f"{name} is NaN")
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class SalienceInputs:
    """What the correction is computed from.

    Only `llm_salience` is a judgement. The rest are observations: how intense the turn
    was, how far the content sits from what is already known, whether the user said
    "覚えておいて", and how often it came up.
    """

    #: The extractor's own estimate. **Weighted lowest of the judged inputs on purpose.**
    llm_salience: float = 0.0
    #: From the turn's expression/TTS markers.
    emotional_intensity: float = 0.0
    #: Distance from existing memories. 1.0 means nothing like it is known yet.
    novelty: float = 0.0
    #: The user asked for it to be remembered.
    explicit_marking: bool = False
    #: How many times it was mentioned.
    repetition: int = 0


def correct_salience(inputs: SalienceInputs) -> float:
    """`base_salience` for a new memory. **Deterministic, and in [0, 1].**"""
    repetition = min(max(inputs.repetition, 0), REPETITION_CAP) / REPETITION_CAP
    return clamp01(
        WEIGHT_LLM * clamp01(inputs.llm_salience, name="llm_salience")
        + WEIGHT_EMOTION * clamp01(inputs.emotional_intensity, name="emotional_intensity")
        + WEIGHT_NOVELTY * clamp01(inputs.novelty, name="novelty")
        + WEIGHT_EXPLICIT * (1.0 if inputs.explicit_marking else 0.0)
        + WEIGHT_REPETITION * repetition,
        name="salience",
    )


def access_boost(access_count: int) -> float:
    """What being recalled adds back. Saturating, so it cannot outgrow the memory itself."""
    if access_count <= 0:
        return 0.0
    return ACCESS_BOOST_CAP * (1.0 - math.exp(-access_count / ACCESS_BOOST_SCALE))


def effective_salience(record: MemoryRecord, now: datetime) -> float:
    """What this memory is worth at `now`. **Exponential decay from its last recall.**

    A `now` before `last_accessed` (a clock that went backwards, a record restored from
    elsewhere) decays by zero rather than growing: **time passing is the only thing that
    may change this value**, and negative elapsed time would make a memory more vivid the
    further the clock drifts.
    """
    elapsed = max((now - record.last_accessed).total_seconds(), 0.0)
    tau = TAU[record.type].total_seconds()
    decayed = record.base_salience * math.exp(-elapsed / tau)
    return min(decayed + access_boost(record.access_count), 1.0)


def is_faded(record: MemoryRecord, now: datetime, *, floor: float = FLOOR) -> bool:
    """Whether this has fallen below the floor and should be archived."""
    return effective_salience(record, now) < floor
