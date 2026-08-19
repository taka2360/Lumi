"""The Provider foundation and its implementations.
**Calls neither an LLM nor an external engine** (HTTP is substituted).

docs/interfaces/provider.md test table 1-6 / 8.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

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
from lumi.providers.llm.ollama import KEEP_ALIVE, OllamaProvider
from lumi.providers.registry import ProviderRegistry
from lumi.providers.stt.base import SAMPLE_RATE
from lumi.providers.stt.faster_whisper import FasterWhisperProvider
from lumi.providers.tts.aivisspeech import TtsError
from lumi.providers.tts.provider import AivisSpeechProvider
from lumi.setup import detect as detect_module
from lumi.setup.detect import detect_ollama, find_on_path
from lumi.setup.state import EngineRuntime
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

    # **`load` is idempotent.** Not called a second time
    assert provider.loads == 1


async def test_a_slow_load_is_not_started_twice() -> None:
    """★ Regression (observed 2026-08-17): **four AivisSpeech processes at once.**

    `load()` being idempotent says nothing about being called concurrently. Starting the
    engine takes ~14 seconds, and every turn arriving inside that window saw
    `is_loaded() == False` — so each one started its own process, **each holding 1 GB of
    the VRAM the LLM is supposed to get** (DESIGN.md §7).
    """

    class Slow(Dummy):
        async def load(self) -> None:
            # Stands in for the engine handshake. **The window where the bug lived**
            await asyncio.sleep(0.01)
            self.loads += 1

    registry = ProviderRegistry()
    provider = Slow()
    registry.register(provider)

    await asyncio.gather(*(registry.get(ProviderKind.LLM) for _ in range(4)))

    assert provider.loads == 1


async def test_waiting_on_one_kind_does_not_block_another() -> None:
    """**Serialized per kind, never globally.** Waiting for the TTS engine must not also
    hold up STT — they are separate processes with separate costs.
    """
    started = asyncio.Event()

    class Blocking(Dummy):
        kind = ProviderKind.TTS

        async def load(self) -> None:
            started.set()
            await asyncio.sleep(3600)

    registry = ProviderRegistry()
    registry.register(Blocking("tts"))
    registry.register(Dummy())

    blocked = asyncio.create_task(registry.get(ProviderKind.TTS))
    await started.wait()
    try:
        async with asyncio.timeout(1.0):
            await registry.get(ProviderKind.LLM)
    finally:
        blocked.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await blocked


def test_registry_refuses_an_unregistered_kind() -> None:
    """**Never silently degrades.** Never produces an unexplained "why isn't it speaking."""
    with pytest.raises(ProviderNotConfigured) as error:
        ProviderRegistry().peek(ProviderKind.TTS)
    # `reason` is the machine-readable cause and is what the warmup logs group by.
    # **A whole sentence there is unmatchable and ungroupable** (`providers/base.py`)
    assert error.value.reason == "no_provider_selected"


def test_selecting_an_unregistered_provider_reports_a_stable_reason() -> None:
    registry = ProviderRegistry()
    registry.register(Dummy("a"))
    with pytest.raises(ProviderNotConfigured) as error:
        registry.select(ProviderKind.LLM, "b")
    assert error.value.reason == "provider_not_registered"
    assert "b" in error.value.detail


def test_registry_reports_only_the_selected_attributions() -> None:
    registry = ProviderRegistry()
    registry.register(Dummy("a"))
    registry.register(Dummy("b"))
    assert [a.display_name for a in registry.attributions()] == ["Dummy"]
    assert len(registry.available(ProviderKind.LLM)) == 2


# ── Ollama ─────────────────────────────────────────────────


async def test_ollama_not_running_is_unavailable() -> None:
    """**Not "not installed."** What's asked of the user is "start it."""
    with pytest.raises(ProviderUnavailable) as error:
        await ollama_with().load()
    assert error.value.reason == "ollama_not_running"


async def test_ollama_without_the_model_is_not_configured() -> None:
    """**The state where `ollama pull` should be suggested.** Prompting to start it would lie."""
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


async def test_ollama_always_states_whether_to_think() -> None:
    """★ **Omitting the field leaves the model's default in place.**

    Hybrid-reasoning models think unless told not to, so `think=False` would be silently
    ignored — and the whole p50 budget would be spent before Lumi says a word
    (measured 2026-08-16: 272 ms vs 5578 ms to the first spoken token).
    """
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=ndjson({"done": True}))

    provider = OllamaProvider("qwen3:8b", transport=transport(handler))
    await provider.load()

    for think in (False, True):
        options = LLMOptions(model="qwen3:8b", think=think)
        async for _ in provider.stream(
            [Message(role="user", content="?")], None, options, CancelToken()
        ):
            pass

    # `load()` also posts to `/api/chat` (the preload). **Only the turns carry `think`**
    turns = [payload for payload in sent if payload["messages"]]
    assert [payload["think"] for payload in turns] == [False, True]


class TestTtsSpeakerIsActuallyLoaded:
    """★ **Deciding a speaker is not loading it** — docs/interfaces/provider.md（表 2c）.

    The engine loads a voice model on its first `audio_query`, which put **3092 ms** inside
    `tts_first_audio_ms` on the first sentence (measured 2026-08-18) against a 200 ms budget.
    """

    def build(self, provider: AivisSpeechProvider, *, engine_default: int) -> list[int]:
        """Substitutes the engine process and the HTTP client. **Neither is started.**"""
        initialized: list[int] = []

        class FakeEngine:
            async def ensure_running(self) -> EngineRuntime:
                return EngineRuntime.READY

        class FakeClient:
            async def default_speaker(self) -> int:
                return engine_default

            async def initialize_speaker(self, speaker: int) -> None:
                initialized.append(speaker)

        object.__setattr__(provider, "_engine", FakeEngine())
        object.__setattr__(provider, "_client", FakeClient())
        return initialized

    async def test_load_initializes_the_voice(self) -> None:
        provider = AivisSpeechProvider(10101)
        initialized = self.build(provider, engine_default=888)

        await provider.load()

        assert initialized == [888], "話者 ID を決めただけで、声を載せていない"

    async def test_it_initializes_the_voice_the_pack_chose(self) -> None:
        """★ **Warming the wrong voice is not warming.**

        `ReactiveLoop._voice()` prefers the Content Pack's speaker, so initializing the
        engine's default instead would leave the voice that actually speaks cold — and the
        first sentence would pay the full 3 s anyway.
        """
        provider = AivisSpeechProvider(10101, speaker=42)
        initialized = self.build(provider, engine_default=888)

        await provider.load()

        assert initialized == [42]
        assert provider.default_voice().speaker == 42

    async def test_a_voice_that_will_not_load_is_not_fatal(self) -> None:
        """**Slow is not broken.** The engine answers, so Lumi still speaks — it just pays
        what it used to. Never silently, though (`tts.speaker_init_failed`).
        """
        provider = AivisSpeechProvider(10101)
        self.build(provider, engine_default=888)

        async def refuse(speaker: int) -> None:
            raise TtsError("speaker_init_failed", "500")

        object.__setattr__(provider._client, "initialize_speaker", refuse)

        await provider.load()

        assert provider.is_loaded(), "初期化に失敗しただけで喋れなくしてはいけない"


class TestOllamaIsActuallyLoaded:
    """★ **`load()` は接続確認ではない** — docs/interfaces/provider.md（表 2c）.

    Confirming that a model exists takes milliseconds while its weights are still on disk.
    A `load()` that stopped there reported success and **handed the first reply a 3767 ms
    bill** (measured 2026-08-18). Every test here fails against that version.
    """

    def recorder(self) -> tuple[list[dict[str, object]], object]:
        sent: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "0.5.0"})
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
            sent.append(json.loads(request.content))
            return httpx.Response(200, content=ndjson({"done": True}))

        return sent, handler

    async def test_load_puts_the_weights_in_memory(self) -> None:
        """**Empty `messages` is Ollama's own "load without generating".**"""
        sent, handler = self.recorder()
        provider = OllamaProvider("qwen3:8b", transport=transport(handler))

        await provider.load()

        assert sent, "load() が推論エンジンに何も要求していない = 重みは載っていない"
        assert sent[0]["messages"] == [], "プリロードのつもりで生成させてはいけない"

    async def test_the_model_is_kept_resident(self) -> None:
        """★ Ollama evicts after 5 minutes by default. **A desktop character idles longer
        than that**, so every conversation after a gap was paying the reload again.
        """
        sent, handler = self.recorder()
        provider = OllamaProvider("qwen3:8b", transport=transport(handler))
        await provider.load()

        async for _ in provider.stream(
            [Message(role="user", content="?")], None, LLMOptions(model="qwen3:8b"), CancelToken()
        ):
            pass

        # **Every request, not just the preload** — the countdown restarts from the last call
        assert [payload["keep_alive"] for payload in sent] == [KEEP_ALIVE, KEEP_ALIVE]

    async def test_unload_gives_the_residency_back(self) -> None:
        """★ Lumi asked for several GB to stay resident. **Lumi gives it back.**

        Without this, quitting Lumi leaves the memory pinned — and it is not Lumi's to keep.
        """
        sent, handler = self.recorder()
        provider = OllamaProvider("qwen3:8b", transport=transport(handler))
        await provider.load()

        await provider.unload()

        assert sent[-1]["keep_alive"] == 0, "常駐を解放していない"

    async def test_the_http_client_is_reused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """★ **Constructing an `AsyncClient` costs ~0.2 s** (SSL context + proxy env).

        One per request put that inside `llm_first_token_ms` on **every** turn: 569 ms → 411 ms
        just by reusing it (2026-08-18). `aivisspeech.py` avoids exactly this; this module
        was doing it.
        """
        built = 0
        real = httpx.AsyncClient

        def counting(*args: object, **kwargs: object) -> httpx.AsyncClient:
            nonlocal built
            built += 1
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(httpx, "AsyncClient", counting)
        _sent, handler = self.recorder()
        provider = OllamaProvider("qwen3:8b", transport=transport(handler))

        await provider.load()
        for _ in range(3):
            async for _event in provider.stream(
                [Message(role="user", content="?")],
                None,
                LLMOptions(model="qwen3:8b"),
                CancelToken(),
            ):
                pass

        assert built == 1, f"ターンごとにクライアントを作り直している（{built} 個）"


async def test_ollama_separates_reasoning_from_text() -> None:
    """**Reasoning is never routed to TTS.** Keeping it a separate type means downstream code can't
    mix it up.
    """
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
    """**cooperative.** Exits at the next checkpoint."""
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
    """**A failure after connecting is never raised as an exception.** It couldn't stay consistent
    with what's already been spoken.
    """

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
    """**Never lets the library download on its own** (ADR-023).

    If missing, it stops in a state that can say "please fetch it."
    """
    provider = FasterWhisperProvider("small", empty_model_dir)
    with pytest.raises(ProviderNotConfigured) as error:
        await provider.load()
    assert error.value.reason == "model_missing"


async def test_an_unpinned_model_name_is_a_different_failure(empty_model_dir: Path) -> None:
    """**"Not pinned" and "not fetched yet" need different answers.**

    One is fixed by running setup; the other can't be fixed by the user at all.
    Collapsing them would send someone to re-download a model that was never offered.
    """
    provider = FasterWhisperProvider("tiny", empty_model_dir)
    with pytest.raises(ProviderNotConfigured) as error:
        await provider.load()
    assert error.value.reason == "unknown_model"


async def test_an_installed_model_that_will_not_build_is_not_reported_as_missing(
    empty_model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ **"Not set up yet" and "broken" ask different things of the user** (`providers/base.py`).

    `_resolve` has already proved every pinned file is on disk, so a failure past that
    point is a CUDA/cuDNN load failure, an unsupported `compute_type`, corrupt weights or
    OOM. Reporting it as `model_missing` tells someone to download a model they have.
    """

    class Installed(FasterWhisperProvider):
        """Stands in for a model whose pinned files are all on disk."""

        def _resolve(self) -> Path:
            return empty_model_dir

    provider = Installed("small", empty_model_dir)

    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("cudnn_ops64_9.dll をロードできない")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=explode))

    with pytest.raises(ProviderUnavailable) as error:
        await provider.load()
    assert error.value.reason == "model_load_failed"
    assert "セットアップで取得" not in error.value.detail


async def test_transcribe_before_load_is_refused(empty_model_dir: Path) -> None:
    provider = FasterWhisperProvider("tiny", empty_model_dir)
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    with pytest.raises(ProviderNotConfigured):
        await provider.transcribe(audio, "ja", CancelToken())


def test_stt_reports_the_device_it_resolved_to(empty_model_dir: Path) -> None:
    """DESIGN.md §7 places STT on the GPU when there is room.

    Measured 2026-08-16: **60 ms on GPU vs 920 ms on CPU** for the same clip. The 0.22 s
    budget is unreachable on CPU, so `resource_hint()` has to say which one is in play —
    Phase 5's `ModelResourceManager` budgets from these numbers.
    """
    hint = FasterWhisperProvider("small", empty_model_dir, device="cpu").resource_hint()
    assert hint.device_pref is DevicePref.CPU_ONLY
    assert hint.vram_estimate_mb == 0


def test_forcing_cpu_is_honoured(empty_model_dir: Path) -> None:
    """**A setting, not a guess.** Small-VRAM machines need the escape hatch (ADR-025)."""
    provider = FasterWhisperProvider("small", empty_model_dir, device="cpu")
    assert provider.resource_hint().device_pref is DevicePref.CPU_ONLY


def test_faster_whisper_download_is_disabled_in_code() -> None:
    """`local_files_only=True` hasn't been removed. **Removing it would make it silently start
    communicating.**
    """
    source = Path(FasterWhisperProvider.__module__.replace(".", "/") + ".py")
    text = (Path(__file__).resolve().parents[1] / source).read_text(encoding="utf-8")
    assert "local_files_only=True" in text


# ── Detecting Ollama (setup's job, not the Provider's) ──────────────


async def test_detect_returns_none_when_ollama_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Detection, not the Provider, decides "not installed."**

    Never actually probes the port (so the result stays the same even if Ollama
    happens to be running on the dev machine).
    """

    async def closed(port: int, *, host: str = "127.0.0.1") -> bool:
        return False

    monkeypatch.setattr(detect_module, "is_port_open", closed)
    assert await detect_ollama({"PATH": ""}) is None


def test_find_on_path_uses_the_given_environment() -> None:
    assert find_on_path("definitely-not-a-real-command", {"PATH": ""}) is None


# ── AivisSpeech ──────────────────────────────────────────


async def test_aivisspeech_synthesize_sets_voice_parameters() -> None:
    # Verify voice parameters from audio_query are overwritten during synthesis
    import io
    import wave

    sent_query: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/audio_query":
            return httpx.Response(
                200,
                json={
                    "accent_phrases": [
                        {
                            "moras": [
                                {
                                    "text": "コ",
                                    "consonant": "k",
                                    "vowel": "o",
                                    "vowel_length": 0.1,
                                }
                            ]
                        }
                    ],
                    "volumeScale": 1.0,
                },
            )
        if request.url.path == "/synthesis":
            nonlocal sent_query
            sent_query = json.loads(request.content)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as sink:
                sink.setnchannels(1)
                sink.setsampwidth(2)
                sink.setframerate(24000)
                sink.writeframes(b"\x00\x00" * 100)
            return httpx.Response(200, content=buf.getvalue())
        return httpx.Response(404)

    from lumi.providers.tts.aivisspeech import AivisSpeechClient

    client = AivisSpeechClient(port=10101)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        audio = await client.synthesize(
            "こんにちは", speaker=0, volume_scale=0.4, speed_scale=1.6
        )
        assert sent_query.get("volumeScale") == 0.4
        assert sent_query.get("speedScale") == 1.6
        assert audio.wav
    finally:
        await client.aclose()
