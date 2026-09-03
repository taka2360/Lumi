"""LLMProvider's contract.

Type definitions → docs/interfaces/provider.md "LLMProvider"

**Never route `reasoning` to TTS.** The content of thinking tags is never spoken.
To that end, text and reasoning are **kept as separate event types** (so downstream
code can't mix them up).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import Provider
from lumi.tools.base import ToolDescriptor


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    #: When `role="tool"`, which call this is a response to
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LLMOptions:
    """How to decode. **Built by `llm/sampling.py`, never field by field at a call site**
    (ADR-048) — a turn assembled from whatever defaults were nearest is a turn nobody can
    reproduce.

    ★ **`None` is not a neutral value. It means "send nothing", and the runtime then takes
    the number from the model's own file** — for `qwen3.5:9b` that is `presence_penalty
    1.5`, which Lumi never asked for. Leaving a field out is therefore a decision, and
    `sampling.py` states every one of them for the families it knows.
    """

    model: str
    temperature: float = 0.7
    #: Nucleus. `None` → the model file decides
    top_p: float | None = None
    #: Candidate cut-off. `None` → the model file decides
    top_k: int | None = None
    min_p: float | None = None
    #: Multiplicative penalty on tokens seen in the last `repeat_last_n`. **1.0 is off**
    repeat_penalty: float | None = None
    #: Additive, one-shot penalty on any token already seen. **Aimed at endless repetition
    #: in long generations** — a short spoken reply pays it on ordinary Japanese instead
    presence_penalty: float | None = None
    #: Additive penalty scaled by how often a token was seen
    frequency_penalty: float | None = None
    #: Runaway guard, not a length control (`sampling.CONVERSATION_MAX_TOKENS`)
    max_tokens: int | None = None
    #: **Never set in production.** A fixed seed makes the same input produce the same
    #: reply forever, which is the opposite of a character. It exists so the A/B harness
    #: (`scripts/llm_profile_eval.py`) can compare profiles rather than compare noise
    seed: int | None = None
    #: Whether to enable reasoning (Qwen3 family). **Even when enabled, never routed to TTS**
    think: bool = False


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Text that becomes speech."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """Reasoning. **Never routed to TTS.** Not shown in the speech bubble either (fine to show in
    the Inspector).
    """

    text: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Finish:
    reason: str
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMFailure:
    """**Broke mid-stream.** A failure before connecting is raised as an exception
    (`ProviderUnavailable`).

    Raising an exception for a mid-stream failure would make it impossible for the
    caller to stay consistent with text already played back (it's already been spoken).
    """

    message: str


LLMEvent = TextDelta | ReasoningDelta | ToolCall | Finish | LLMFailure


class LLMProvider(Provider, Protocol):
    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDescriptor] | None,
        options: LLMOptions,
        cancel_token: CancelToken,
    ) -> AsyncIterator[LLMEvent]:
        """**`cancel_token` is `cooperative`.** Stops at the next checkpoint."""
        ...
