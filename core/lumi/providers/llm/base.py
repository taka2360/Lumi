"""LLMProvider の契約。

型の定義 → docs/interfaces/provider.md「LLMProvider」

**`reasoning` を TTS に流さない。** 思考タグの内容は音声化しない。
そのために、テキストと思考を**イベントの型で分ける**（後段が取り違えられないように）。
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
    #: `role="tool"` のとき、どの呼び出しへの応答か
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LLMOptions:
    model: str
    temperature: float = 0.8
    max_tokens: int | None = None
    #: 思考を有効にするか（Qwen3 系）。**有効でも TTS には流さない**
    think: bool = False


@dataclass(frozen=True, slots=True)
class TextDelta:
    """発話になるテキスト。"""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """思考。**TTS に流さない。** 吹き出しにも出さない（Inspector には出してよい）。"""

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
    """**ストリームの途中で壊れた。** 接続前の失敗は例外（`ProviderUnavailable`）で上げる。

    途中で失敗したことを例外にすると、それまでに再生したテキストとの
    整合が呼び出し側で取れなくなる（もう喋ってしまっている）。
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
        """**`cancel_token` は `cooperative`。** 次のチェックポイントで止まる。"""
        ...
