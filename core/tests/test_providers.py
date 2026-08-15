"""Provider 基盤と各実装。**LLM も外部エンジンも呼ばない**（HTTP を差し替える）。

docs/interfaces/provider.md のテスト表 1〜6 / 8。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import numpy as np
import pytest

from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderKind,
    ProviderNotConfigured,
    ProviderUnavailable,
    ResourceHint,
    UnloadPolicy,
)
from lumi.providers.llm.base import (
    Finish,
    LLMFailure,
    LLMOptions,
    Message,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)
from lumi.providers.llm.ollama import OllamaProvider
from lumi.providers.registry import ProviderRegistry
from lumi.providers.stt.base import SAMPLE_RATE
from lumi.providers.stt.faster_whisper import FasterWhisperProvider
from lumi.setup import detect as detect_module
from lumi.setup.detect import detect_ollama, find_on_path
from lumi.tools.base import ToolDescriptor, ToolKind


def ndjson(*chunks: dict[str, object]) -> bytes:
    return "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks).encode()


def transport(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


def ollama_with(
    *,
    version: dict[str, object] | None = None,
    tags: dict[str, object] | None = None,
    chat: bytes | None = None,
    model: str = "qwen3:8b",
) -> OllamaProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            if version is None:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json=version)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags or {"models": []})
        if request.url.path == "/api/chat":
            return httpx.Response(200, content=chat or b"")
        return httpx.Response(404)

    return OllamaProvider(model, transport=transport(handler))


# ── Provider Registry ───────────────────────────────────────


class Dummy:
    kind = ProviderKind.LLM

    def __init__(self, provider_id: str = "dummy") -> None:
        self.id = provider_id
        self.loads = 0

    async def load(self) -> None:
        self.loads += 1

    async def unload(self) -> None:
        self.loads = 0

    def is_loaded(self) -> bool:
        return self.loads > 0

    def resource_hint(self) -> ResourceHint:
        return ResourceHint(DevicePref.CPU_ONLY, 0, 0, UnloadPolicy.PINNED)

    def attribution(self) -> Attribution:
        return Attribution(display_name="Dummy", credit_text="d", license_name="MIT")


async def test_registry_loads_on_first_get() -> None:
    registry = ProviderRegistry()
    provider = Dummy()
    registry.register(provider)

    await registry.get(ProviderKind.LLM)
    await registry.get(ProviderKind.LLM)

    # **`load` は冪等。** 2回目は呼ばれない
    assert provider.loads == 1


def test_registry_refuses_an_unregistered_kind() -> None:
    """**黙って劣化しない。** 「なぜか喋らない」を作らない。"""
    with pytest.raises(ProviderNotConfigured):
        ProviderRegistry().peek(ProviderKind.TTS)


def test_registry_reports_only_the_selected_attributions() -> None:
    registry = ProviderRegistry()
    registry.register(Dummy("a"))
    registry.register(Dummy("b"))
    assert [a.display_name for a in registry.attributions()] == ["Dummy"]
    assert len(registry.available(ProviderKind.LLM)) == 2


# ── Ollama ─────────────────────────────────────────────────


async def test_ollama_not_running_is_unavailable() -> None:
    """**「入っていない」ではない。** ユーザーに求めるのは「起動」。"""
    with pytest.raises(ProviderUnavailable) as error:
        await ollama_with().load()
    assert error.value.reason == "ollama_not_running"


async def test_ollama_without_the_model_is_not_configured() -> None:
    """**`ollama pull` を案内すべき状態。** 起動を促すのは嘘になる。"""
    with pytest.raises(ProviderNotConfigured) as error:
        await ollama_with(version={"version": "0.5.0"}, tags={"models": []}).load()
    assert error.value.reason == "model_missing"


async def test_ollama_accepts_a_model_without_its_tag() -> None:
    provider = ollama_with(version={"version": "0.5.0"}, tags={"models": [{"name": "qwen3:8b"}]})
    await provider.load()
    assert provider.is_loaded()


async def test_ollama_streams_text_and_finishes() -> None:
    provider = ollama_with(
        version={"version": "0.5.0"},
        tags={"models": [{"name": "qwen3:8b"}]},
        chat=ndjson(
            {"message": {"role": "assistant", "content": "こん"}, "done": False},
            {"message": {"role": "assistant", "content": "にちは"}, "done": False},
            {"done": True, "done_reason": "stop", "eval_count": 7},
        ),
    )
    await provider.load()

    events = [
        event
        async for event in provider.stream(
            [Message(role="user", content="やあ")],
            None,
            LLMOptions(model="qwen3:8b"),
            CancelToken(),
        )
    ]

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["こん", "にちは"]
    finish = events[-1]
    assert isinstance(finish, Finish)
    assert finish.usage["completion_tokens"] == 7


async def test_ollama_separates_reasoning_from_text() -> None:
    """**思考は TTS に流さない。** 型で分けておけば、後段が取り違えられない。"""
    provider = ollama_with(
        version={"version": "0.5.0"},
        tags={"models": [{"name": "qwen3:8b"}]},
        chat=ndjson(
            {"message": {"thinking": "ええと", "content": ""}, "done": False},
            {"message": {"content": "はい"}, "done": False},
            {"done": True},
        ),
    )
    await provider.load()

    events = [
        event
        async for event in provider.stream(
            [Message(role="user", content="?")], None, LLMOptions(model="qwen3:8b"), CancelToken()
        )
    ]

    assert [e.text for e in events if isinstance(e, ReasoningDelta)] == ["ええと"]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["はい"]


async def test_ollama_parses_tool_calls() -> None:
    provider = ollama_with(
        version={"version": "0.5.0"},
        tags={"models": [{"name": "qwen3:8b"}]},
        chat=ndjson(
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "character.set_expression",
                                "arguments": {"emotion": "happy"},
                            }
                        }
                    ]
                },
                "done": False,
            },
            {"done": True},
        ),
    )
    await provider.load()

    calls = [
        event
        async for event in provider.stream(
            [Message(role="user", content="笑って")],
            [
                ToolDescriptor(
                    name="character.set_expression",
                    description="表情",
                    input_schema={"type": "object"},
                    kind=ToolKind.CONTROL,
                )
            ],
            LLMOptions(model="qwen3:8b"),
            CancelToken(),
        )
        if isinstance(event, ToolCall)
    ]

    assert calls == [
        ToolCall(id="call_0", name="character.set_expression", arguments={"emotion": "happy"})
    ]


async def test_ollama_stops_when_the_token_fires() -> None:
    """**cooperative。** 次のチェックポイントで抜ける。"""
    provider = ollama_with(
        version={"version": "0.5.0"},
        tags={"models": [{"name": "qwen3:8b"}]},
        chat=ndjson(
            {"message": {"content": "あ"}, "done": False},
            {"message": {"content": "い"}, "done": False},
            {"done": True},
        ),
    )
    await provider.load()
    token = CancelToken()

    texts: list[str] = []
    async for event in provider.stream(
        [Message(role="user", content="?")], None, LLMOptions(model="qwen3:8b"), token
    ):
        if isinstance(event, TextDelta):
            texts.append(event.text)
            token.fire("user_speech")

    assert texts == ["あ"]


async def test_ollama_reports_a_broken_stream_as_an_event() -> None:
    """**接続後の失敗は例外にしない。** もう喋ってしまっている分と整合が取れなくなる。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            raise httpx.ReadError("boom")
        return httpx.Response(200, json={"version": "0.5.0"})

    provider = OllamaProvider("qwen3:8b", transport=transport(handler))
    events = [
        event
        async for event in provider.stream(
            [Message(role="user", content="?")], None, LLMOptions(model="qwen3:8b"), CancelToken()
        )
    ]
    assert len(events) == 1
    assert isinstance(events[0], LLMFailure)


def test_ollama_is_external_to_the_vram_budget() -> None:
    hint = ollama_with().resource_hint()
    assert hint.device_pref is DevicePref.EXTERNAL_PROCESS
    assert hint.vram_estimate_mb == 0


def test_ollama_attribution_names_the_model() -> None:
    assert "qwen3:8b" in ollama_with().attribution().credit_text


# ── faster-whisper ──────────────────────────────────────────


@pytest.fixture
def empty_model_dir(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "models"


async def test_missing_stt_model_fails_loudly(empty_model_dir: Path) -> None:
    """**ライブラリに勝手にダウンロードさせない**（ADR-023）。

    無ければ「取得してください」と言える状態で止まる。
    """
    provider = FasterWhisperProvider("tiny", empty_model_dir)
    with pytest.raises(ProviderNotConfigured) as error:
        await provider.load()
    assert error.value.reason == "model_missing"


async def test_transcribe_before_load_is_refused(empty_model_dir: Path) -> None:
    provider = FasterWhisperProvider("tiny", empty_model_dir)
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    with pytest.raises(ProviderNotConfigured):
        await provider.transcribe(audio, "ja", CancelToken())


def test_stt_stays_off_the_gpu(empty_model_dir: Path) -> None:
    """**GPU は LLM に全振りする**（DESIGN.md §7）。"""
    hint = FasterWhisperProvider("tiny", empty_model_dir).resource_hint()
    assert hint.device_pref is DevicePref.CPU_ONLY
    assert hint.vram_estimate_mb == 0


def test_faster_whisper_download_is_disabled_in_code() -> None:
    """`local_files_only=True` が消えていないこと。**これが消えると黙って通信し始める。**"""
    source = Path(FasterWhisperProvider.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[1] / source).read_text(encoding="utf-8")
    assert "local_files_only=True" in text


# ── Ollama の検出（Provider ではなく setup の担当）──────────────


async def test_detect_returns_none_when_ollama_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """**「入っていない」を Provider ではなく検出が決める。**

    ポートを実際に叩かない（開発機で Ollama が動いていても結果が変わらないように）。
    """

    async def closed(port: int, *, host: str = "127.0.0.1") -> bool:
        return False

    monkeypatch.setattr(detect_module, "is_port_open", closed)
    assert await detect_ollama({"PATH": ""}) is None


def test_find_on_path_uses_the_given_environment() -> None:
    assert find_on_path("definitely-not-a-real-command", {"PATH": ""}) is None
