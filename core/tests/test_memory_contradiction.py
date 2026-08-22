"""Who wins when Lumi is told something that contradicts what it already believes.

**docs/architecture/memory.md §6 / tests 5.** The comparison is a pure function because
the alternative is asking an LLM, which answers differently on Tuesday — and this answer
decides what Lumi believes about the user for months.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lumi.memory.contradiction import Resolution, contradiction_note, normalize, resolve
from lumi.memory.records import AssertionMode, MemoryCandidate, MemoryRecord, MemoryType
from lumi.provenance import ProvenanceClass, TrustLevel

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def existing(mode: AssertionMode, content: str = "Factorio が好き") -> MemoryRecord:
    return MemoryRecord(
        id="m1",
        type=MemoryType.SEMANTIC,
        subject="user.hobby",
        content=content,
        assertion_mode=mode,
        evidence_ref=("u1",),
        confidence=0.9,
        provenance_class=ProvenanceClass.TRUSTED,
        trust_level=TrustLevel.TRUSTED,
        base_salience=0.6,
        created_at=NOW,
        last_accessed=NOW,
        access_count=0,
        archived_at=None,
        valid_from=NOW,
        superseded_by=None,
    )


def candidate(
    mode: AssertionMode, content: str = "最近は Rimworld をやっている"
) -> MemoryCandidate:
    return MemoryCandidate(
        type=MemoryType.SEMANTIC,
        subject="user.hobby",
        content=content,
        assertion_mode=mode,
        provenance_class=ProvenanceClass.TRUSTED,
        trust_level=TrustLevel.TRUSTED,
        evidence_ref=("u2",),
    )


# ── The ordering ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (AssertionMode.INFERRED, AssertionMode.USER_STATED, Resolution.SUPERSEDE),
        (AssertionMode.SELF_GENERATED, AssertionMode.INFERRED, Resolution.SUPERSEDE),
        (AssertionMode.EXTERNAL, AssertionMode.SELF_GENERATED, Resolution.SUPERSEDE),
        (AssertionMode.USER_CONFIRMED, AssertionMode.USER_STATED, Resolution.KEEP_WEAK),
        (AssertionMode.USER_STATED, AssertionMode.INFERRED, Resolution.KEEP_WEAK),
        (AssertionMode.INFERRED, AssertionMode.SELF_GENERATED, Resolution.KEEP_WEAK),
    ],
)
def test_the_better_grounded_claim_wins(
    old: AssertionMode, new: AssertionMode, expected: Resolution
) -> None:
    """★ `user_confirmed > user_stated > inferred > self_generated > external`.

    **The question is not who is more confident but who is in a position to know.**
    """
    assert resolve(existing(old), candidate(new)) is expected


def test_equal_grounds_let_the_newer_claim_win() -> None:
    """★ **People change their minds.** Two `user_stated` beliefs a year apart are not a
    contradiction to adjudicate; treating them as one would leave Lumi arguing that the
    user still likes what they liked in March.
    """
    assert resolve(existing(AssertionMode.USER_STATED), candidate(AssertionMode.USER_STATED)) is (
        Resolution.SUPERSEDE
    )


def test_a_guess_does_not_overwrite_what_the_user_said() -> None:
    """It is kept — a guess that turns out right should still be findable — but it does
    not become the live belief.
    """
    assert resolve(existing(AssertionMode.USER_STATED), candidate(AssertionMode.INFERRED)) is (
        Resolution.KEEP_WEAK
    )


# ── Saying the same thing again ──────────────────────────────


def test_the_same_thing_said_again_is_not_a_contradiction() -> None:
    same = candidate(AssertionMode.USER_STATED, content="Factorio が好き")

    assert resolve(existing(AssertionMode.USER_STATED), same) is Resolution.DUPLICATE


def test_only_whitespace_is_normalized_away() -> None:
    """★ Deciding that two differently worded sentences mean the same thing needs
    embeddings (2e). **Until then Lumi cannot tell** — which is different from having
    checked and found nothing, and the code says the honest one.
    """
    assert normalize("  Factorio  が\n好き ") == "Factorio が 好き"

    reworded = candidate(AssertionMode.USER_STATED, content="Factorio が大好き")
    assert resolve(existing(AssertionMode.USER_STATED), reworded) is Resolution.SUPERSEDE


# ── The note ─────────────────────────────────────────────────


def test_the_note_holds_both_sides() -> None:
    """It exists to be read back into a prompt — **"前は Factorio 好きって言ってたけど"** —
    so it has to carry what the belief used to be, not just that something changed.
    """
    note = contradiction_note(existing(AssertionMode.USER_STATED), "最近は Rimworld をやっている")

    assert "Factorio が好き" in note
    assert "最近は Rimworld をやっている" in note
    assert "user.hobby" in note
