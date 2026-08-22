"""Forgetting, as arithmetic. **docs/architecture/memory.md §4 / §5, tests 1 and 11.**

Every function here is pure, which is the point: what Lumi still knows about you in a
month is decided by these curves, and a rule that could only be observed by leaving the
program running for six weeks would ship unverified.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from lumi.memory.decay import (
    ACCESS_BOOST_CAP,
    FLOOR,
    REPETITION_CAP,
    TAU,
    SalienceInputs,
    access_boost,
    correct_salience,
    effective_salience,
    is_faded,
)
from lumi.memory.records import AssertionMode, MemoryRecord, MemoryType
from lumi.provenance import ProvenanceClass, TrustLevel

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def memory(
    *,
    kind: MemoryType = MemoryType.SEMANTIC,
    base_salience: float = 1.0,
    last_accessed: datetime = NOW,
    access_count: int = 0,
) -> MemoryRecord:
    return MemoryRecord(
        id="m1",
        type=kind,
        subject="user.hobby",
        content="Factorio が好き",
        assertion_mode=AssertionMode.USER_STATED,
        evidence_ref=("u1",),
        confidence=0.8,
        provenance_class=ProvenanceClass.TRUSTED,
        trust_level=TrustLevel.TRUSTED,
        base_salience=base_salience,
        created_at=last_accessed,
        last_accessed=last_accessed,
        access_count=access_count,
        archived_at=None,
        valid_from=last_accessed,
        superseded_by=None,
    )


# ── The decay curve ──────────────────────────────────────────


def test_one_tau_leaves_the_expected_fraction() -> None:
    """The curve is `exp(-dt/tau)`, so one τ is 1/e of what it was."""
    record = memory(last_accessed=NOW - TAU[MemoryType.SEMANTIC])

    assert effective_salience(record, NOW) == pytest.approx(1.0 / math.e)


def test_nothing_fades_at_the_moment_it_is_written() -> None:
    assert effective_salience(memory(), NOW) == pytest.approx(1.0)


def test_episodic_fades_faster_than_semantic() -> None:
    """★ **"先週こんな話をした" は薄れ、"Factorio が好き" は残る** — the difference between
    the two layers is the whole reason there are two.
    """
    # Two months, at the strongest a memory can be formed. Episodic is past the floor by
    # then (14 · ln 20 ≈ 42 days); semantic has barely moved.
    two_months_ago = NOW - timedelta(days=60)
    episodic = effective_salience(
        memory(kind=MemoryType.EPISODIC, last_accessed=two_months_ago), NOW
    )
    semantic = effective_salience(
        memory(kind=MemoryType.SEMANTIC, last_accessed=two_months_ago), NOW
    )

    assert episodic < semantic
    assert episodic < FLOOR < semantic


def test_a_clock_that_went_backwards_does_not_make_a_memory_more_vivid() -> None:
    """★ Time passing is the only thing that may change this value. A `now` before
    `last_accessed` (clock skew, a restored file) must decay by zero, **not by less than
    zero** — otherwise a drifting clock revives everything Lumi had begun to forget.
    """
    record = memory(base_salience=0.4, last_accessed=NOW)

    assert effective_salience(record, NOW - timedelta(days=400)) == pytest.approx(0.4)


# ── Being recalled ───────────────────────────────────────────


def test_recall_adds_back_but_cannot_exceed_its_cap() -> None:
    """Otherwise anything retrieved often enough becomes permanent — and what gets
    retrieved often is partly a function of what retrieval already favours.
    """
    assert access_boost(0) == 0.0
    assert 0 < access_boost(1) < ACCESS_BOOST_CAP
    assert access_boost(1000) == pytest.approx(ACCESS_BOOST_CAP, abs=1e-6)


def test_a_recalled_memory_outlives_one_that_was_never_asked_for() -> None:
    long_ago = NOW - timedelta(days=90)
    forgotten = memory(kind=MemoryType.EPISODIC, last_accessed=long_ago)
    recalled = memory(kind=MemoryType.EPISODIC, last_accessed=long_ago, access_count=20)

    assert is_faded(forgotten, NOW)
    assert not is_faded(recalled, NOW)


def test_salience_never_exceeds_one() -> None:
    """`base + boost` could otherwise leave the scale everything else is compared on."""
    record = memory(base_salience=1.0, access_count=1000)

    assert effective_salience(record, NOW) == pytest.approx(1.0)


# ── The floor ────────────────────────────────────────────────


def test_the_floor_is_a_strict_boundary() -> None:
    """Exactly at the floor is still remembered. **Fading is what falls below it.**"""
    at_floor = memory(base_salience=FLOOR, last_accessed=NOW)

    assert not is_faded(at_floor, NOW)
    assert is_faded(memory(base_salience=FLOOR / 2, last_accessed=NOW), NOW)


# ── The deterministic correction ─────────────────────────────


def test_the_llm_alone_cannot_produce_a_strong_memory() -> None:
    """★ **The extractor's own number is 40% of the answer** (docs/architecture/memory.md
    §4). It scores the same utterance 0.8 on one run and 0.4 on the next; the remaining
    inputs are counted rather than judged.
    """
    only_the_llm = correct_salience(SalienceInputs(llm_salience=1.0))

    assert only_the_llm == pytest.approx(0.4)


def test_being_asked_to_remember_counts_for_something() -> None:
    """「覚えておいて」 is an observation, not a judgement, and it is weighted like one."""
    plain = correct_salience(SalienceInputs(llm_salience=0.5))
    asked = correct_salience(SalienceInputs(llm_salience=0.5, explicit_marking=True))

    assert asked - plain == pytest.approx(0.15)


def test_every_input_at_its_maximum_is_exactly_one() -> None:
    """The weights sum to 1.0, so a memory cannot be more salient than the scale."""
    everything = correct_salience(
        SalienceInputs(
            llm_salience=1.0,
            emotional_intensity=1.0,
            novelty=1.0,
            explicit_marking=True,
            repetition=REPETITION_CAP,
        )
    )

    assert everything == pytest.approx(1.0)


def test_repetition_stops_counting_at_the_cap() -> None:
    """Saying something five times and fifty times both mean "this matters". Without the
    cap the term would flatten every other one.
    """
    five = correct_salience(SalienceInputs(repetition=REPETITION_CAP))
    fifty = correct_salience(SalienceInputs(repetition=REPETITION_CAP * 10))

    assert five == fifty == pytest.approx(0.10)


def test_an_out_of_range_score_is_rounded_rather_than_rejected() -> None:
    """★ A model that answers 1.4 has still said "very". **Losing the memory over a
    formatting slip would be the worse failure**, and the value is clamped into range.
    """
    assert correct_salience(SalienceInputs(llm_salience=1.4)) == pytest.approx(0.4)
    assert correct_salience(SalienceInputs(llm_salience=-3.0)) == pytest.approx(0.0)
    assert correct_salience(SalienceInputs(repetition=-5)) == pytest.approx(0.0)


def test_not_a_number_is_refused() -> None:
    """**Clamping `NaN` would quietly turn it into a number.** It is not one."""
    with pytest.raises(ValueError, match="llm_salience"):
        correct_salience(SalienceInputs(llm_salience=float("nan")))


def test_the_correction_is_deterministic() -> None:
    """Same inputs, same answer — every time, in any order."""
    inputs = SalienceInputs(llm_salience=0.7, emotional_intensity=0.3, novelty=0.9, repetition=2)

    assert {correct_salience(inputs) for _ in range(10)} == {correct_salience(inputs)}
