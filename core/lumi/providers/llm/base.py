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
    model: str
    temperature: float = 0.8
    max_tokens: int | None = None
    #: Whether to enable reasoning (Qwen3 family). **Even when enabled, never routed to TTS**
    think: bool = False


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Text that becomes speech."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """Reasoning. **Never routed to TTS.** Not shown in the speech bubble either (fine to show in the Inspector)."""

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
