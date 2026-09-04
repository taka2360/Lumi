"""Sampling profiles — **how the model is told to decode, decided by Core.**

Definition → docs/interfaces/provider.md "LLMOptions と sampling プロファイル" /
Decision → docs/decisions/ADR-048-sampling-profiles.md

## Why this is not "just defaults"

**A field left out of the request is not a neutral field.** Ollama fills it from the
model's own Modelfile, and `qwen3.5:9b` ships `temperature 1 / top_k 20 / top_p 0.95 /
presence_penalty 1.5` (verified 2026-09-02 via `/api/show`). Lumi was sending
`temperature` alone, so **three sampling parameters were being chosen by a file Lumi
never reads**, and nothing in the codebase said so.

`presence_penalty 1.5` is Qwen's own recommendation, and it is the right one for the task
it was written for — long generations that run into endless repetition. **A spoken reply
of one or two sentences is the opposite task**: the penalty is additive over the last
`repeat_last_n` (64) tokens, so it is spent on the particles, the copula and the words the
user just said — the parts of Japanese that are *supposed* to recur.

## `None` means "do not send", and that is why the profiles are exhaustive

Every field a profile leaves at `None` is a field the model's Modelfile decides. **Any
profile that wants to be reproducible has to state every value**, even the ones that
happen to match the current default — a default is a fact about today's model file, not a
decision. The generic profile is the deliberate exception: for a model Lumi knows nothing
about, its own author's values are a better guess than Qwen's.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import Final

from lumi.providers.llm.base import LLMOptions


class Purpose(StrEnum):
    """What the generation is for. **Not a model and not a Provider** — the same model
    decodes differently depending on whether a person is waiting for it to speak.
    """

    #: The reply the user hears. Natural Japanese, in the persona's voice
    CONVERSATION = "conversation"
    #: `memory/extraction_prompt.py` — JSON, read by a parser and never spoken
    EXTRACTION = "extraction"


#: Runaway guard, in tokens. **Not a style control** — the reply is kept short by
#: `SPEECH_PROTOCOL`, and a cap tight enough to shape length would cut a sentence in half
#: mid-speech. Measured replies run 30-120 tokens (2026-09-02), so this is roughly 4x the
#: worst observed case: it only ever fires on a model that has stopped terminating.
CONVERSATION_MAX_TOKENS: Final = 512

#: Runaway guard for extraction (ADR-049). **Derived, not measured**: `CANDIDATE_LIMIT` is
#: 8, and anything past it the parser throws away, so the largest answer that can be *read*
#: is about 850 tokens — a JSON element with a Japanese `content` runs 90-100. This is 1.8x
#: that, so it does not fire on a real extraction and does stop one that never terminates.
#:
#: ★ **A cap here only works because `memory/reflection.py` has somewhere to put a
#: truncation.** Extraction reads the same episodes again next pass, so a cut-off that
#: depends only on the input recurs on that input forever; the cap is safe because the
#: reflection pass answers `length` by halving the batch, not by trying again unchanged.
EXTRACTION_MAX_TOKENS: Final = 1536

#: Qwen3 family, non-thinking. **Qwen's published values, with one departure**
#: (docs/interfaces/provider.md). Applies to `qwen3`, `qwen3.5`, `qwen3.6`, `qwen3.8`:
#: the non-thinking recommendation has been the same across the family.
_QWEN3: Final[dict[Purpose, LLMOptions]] = {
    Purpose.CONVERSATION: LLMOptions(
        model="",
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        min_p=0.0,
        # Qwen says 1.0 — **the repetition control it wants is `presence_penalty`.**
        # Ollama 0.33.2's own default is already 1.0 (verified 2026-09-02), but a value
        # this load-bearing is not left to a runtime's release notes
        repeat_penalty=1.0,
        # ★ **The one departure from the model card**, which recommends 1.5.
        # See the module docstring and ADR-048: 1.5 is aimed at endless repetition in long
        # generations, and Qwen's own card warns it can cause language mixing. A/B on the
        # five fixed Japanese cases (docs/measurements/phase2.md) is where the number
        # came from, not from taste
        presence_penalty=0.0,
        frequency_penalty=0.0,
        max_tokens=CONVERSATION_MAX_TOKENS,
    ),
    Purpose.EXTRACTION: LLMOptions(
        model="",
        # **Not a conversation.** The output is parsed, and phase2.md's extraction numbers
        # were taken at 0.2 — the code had been running it at 0.8 since, which is where
        # "subject と assertion_mode が実行ごとに揺れる" comes from
        temperature=0.2,
        top_p=0.8,
        top_k=20,
        min_p=0.0,
        repeat_penalty=1.0,
        # ★ **Must be 0 here.** A JSON array repeats its own keys by construction, and
        # penalising `"subject"` for having appeared is penalising the format itself
        presence_penalty=0.0,
        frequency_penalty=0.0,
        # ★ **Wider than conversation, and load-bearing in a different way** — see
        # `EXTRACTION_MAX_TOKENS`. What a truncated extraction does to the watermark is a
        # memory-layer decision and lives in docs/architecture/memory.md §4 (ADR-049)
        max_tokens=EXTRACTION_MAX_TOKENS,
    ),
}

#: Any model Lumi has no measurements for. **Temperature only** — see the module docstring:
#: guessing a stranger's sampling from Qwen's card is worse than leaving the author's own
#: values in place.
_GENERIC: Final[dict[Purpose, LLMOptions]] = {
    Purpose.CONVERSATION: LLMOptions(model="", temperature=0.7),
    Purpose.EXTRACTION: LLMOptions(model="", temperature=0.2),
}

_QWEN3_MODEL_NAMES: Final[frozenset[str]] = frozenset({"qwen3", "qwen3.5", "qwen3.6", "qwen3.8"})


def options_for(model: str, purpose: Purpose) -> LLMOptions:
    """The generation settings for one model and one purpose.

    **Resolved per model**, because a profile is a claim about a specific family that has
    been measured — swapping the model must swap the settings with it, or Lumi would be
    decoding Gemma with numbers taken from Qwen's model card.
    """
    table = _QWEN3 if _is_qwen3(model) else _GENERIC
    return replace(table[purpose], model=model)


def _is_qwen3(model: str) -> bool:
    """`qwen3.5:9b` → yes, `qwen3.5` → yes, `gemma3:12b` → no.

    Matched exactly on the name before the tag. Similar names such as `qwen3-coder` stay
    generic until they have their own measured profile.
    """
    return model.split(":", 1)[0].strip().lower() in _QWEN3_MODEL_NAMES
