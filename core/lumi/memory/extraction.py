"""What the model answered, checked. **Tolerant about the wrapper, strict about the facts.**

Design → docs/architecture/memory.md §4 / Invariant 7

Models fence JSON in ```json blocks and write a sentence before it. That is a formatting
habit, not a disagreement about the data, so the wrapper is forgiven. A malformed element
is not: it is refused rather than repaired, because a guess about what the model meant
would become a memory nobody stated.

## What the model is not allowed to decide

| | |
|---|---|
| The LLM | what was said, about whom, and how sure it sounds |
| **Core** | whether the evidence exists, what trust it carries, and what it is worth |

**Trust is computed from the cited utterances, never taken from the model** — an extractor
has no standing to say how trustworthy its own source was (Invariant 7). `user_confirmed`
is refused outright: the memory UI is its only source, and a model allowed to claim it
would be a second escalation path. Salience is the deterministic correction from
`memory.decay`, into which the model's own estimate goes at 40%.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final

from lumi.memory.decay import SalienceInputs, correct_salience
from lumi.memory.phrases import explicit_marking
from lumi.memory.records import AssertionMode, MemoryCandidate, MemoryType
from lumi.provenance import join_all, provenance_from
from lumi.storage.memory import Utterance

#: The most memories one pass will accept from one prompt. A model that answers with fifty
#: "facts" about a two-line conversation has not extracted anything.
CANDIDATE_LIMIT: Final = 8


#: Assertion modes an extractor may claim. **`user_confirmed` is not here** — the memory
#: UI is its only source (Invariant 7), and a model allowed to claim it would be a second
#: escalation path. `external` is not here either: nothing outside the conversation is in
#: this prompt.
EXTRACTABLE: Final[Mapping[str, AssertionMode]] = {
    AssertionMode.USER_STATED.value: AssertionMode.USER_STATED,
    AssertionMode.INFERRED.value: AssertionMode.INFERRED,
    AssertionMode.SELF_GENERATED.value: AssertionMode.SELF_GENERATED,
}


class ReflectionRejected(ValueError):
    """One extracted item is not usable. **The pass continues without it.**"""


def parse_extractions(text: str) -> tuple[list[Mapping[str, Any]], list[str]]:
    """The model's answer as a list of items, plus what could not be read.

    **Tolerant about the wrapper, strict about the contents.** Models fence JSON in
    ```json blocks and add a sentence before it; that is a formatting habit, not a
    disagreement about the data. A malformed element, though, is refused rather than
    repaired — a guess about what the model meant would be a memory nobody stated.
    """
    payload = _json_array(text)
    if payload is None:
        return [], ["no_json_array"]
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        return [], [f"invalid_json: {error.msg}"]
    if not isinstance(parsed, list):
        return [], ["not_an_array"]

    items: list[Mapping[str, Any]] = []
    rejected: list[str] = []
    for element in parsed[:CANDIDATE_LIMIT]:
        if isinstance(element, dict):
            items.append(element)
        else:
            rejected.append("not_an_object")
    if len(parsed) > CANDIDATE_LIMIT:
        # **A model that found fifty facts in a two-line conversation found none.**
        rejected.append(f"over_limit: {len(parsed)}")
    return items, rejected


def _json_array(text: str) -> str | None:
    """The outermost JSON array in the model's reply, if there is one."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    body = fenced.group(1) if fenced else text
    start = body.find("[")
    end = body.rfind("]")
    if start == -1 or end <= start:
        return None
    return body[start : end + 1]


def to_candidate(
    item: Mapping[str, Any],
    *,
    lines: Mapping[str, Utterance],
    episode_id: str,
    novelty: float = 0.0,
) -> MemoryCandidate:
    """One extracted item as something the store can be asked to believe.

    Raises `ReflectionRejected` for anything that cannot be checked. **Trust is computed
    from the cited utterances**, never taken from the model: an extractor has no standing
    to say how trustworthy its own source was (Invariant 7).

    **There is no `now` here on purpose.** A memory is valid from when it was *said*, not
    from when reflection got around to reading it, so `valid_from` comes from the cited
    utterances. Passing the clock in would invite the two to drift apart.
    """
    subject = str(item.get("subject", "")).strip()
    content = str(item.get("content", "")).strip()
    if not subject or not content:
        raise ReflectionRejected("missing_subject_or_content")

    mode = EXTRACTABLE.get(str(item.get("assertion_mode", "")))
    if mode is None:
        raise ReflectionRejected(f"unusable_assertion_mode: {item.get('assertion_mode')!r}")

    cited = [str(reference) for reference in item.get("evidence", []) or []]
    evidence = tuple(dict.fromkeys(reference for reference in cited if reference in lines))
    if not evidence:
        # **A citation that does not resolve is not weak evidence, it is none.** Models
        # invent plausible-looking ids, and a memory built on one cannot be traced back.
        raise ReflectionRejected("no_resolvable_evidence")

    supporting = [lines[reference] for reference in evidence]
    trust = join_all(line.trust_level for line in supporting)
    provenance = provenance_from(trust, (line.provenance_class for line in supporting))

    kind = MemoryType.EPISODIC if str(item.get("type")) == "episodic" else MemoryType.SEMANTIC
    salience = correct_salience(
        SalienceInputs(
            llm_salience=_number(item.get("salience"), default=0.5),
            # **Not available yet.** Expression markers are stripped before an utterance is
            # stored, so there is nothing here to read intensity from; Phase 3's internal
            # state is where it will come from. Zero is honest; a guess would not be.
            emotional_intensity=0.0,
            # Supplied by the caller, the only side that can ask the store. **The default
            # is the cautious one**: novelty nobody looked up adds nothing.
            novelty=novelty,
            explicit_marking=explicit_marking(supporting),
            repetition=len(evidence),
        )
    )
    return MemoryCandidate(
        type=kind,
        subject=subject,
        content=content,
        assertion_mode=mode,
        provenance_class=provenance,
        trust_level=trust,
        evidence_ref=evidence,
        confidence=_number(item.get("confidence"), default=0.5),
        base_salience=salience,
        valid_from=max(line.occurred_at for line in supporting),
        source_episode_ids=(episode_id,),
    )


def _number(value: Any, *, default: float) -> float:
    """A float the model may have written as a string, or not at all."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
