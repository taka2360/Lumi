"""Warming the three inference engines, and the gate it feeds.

Design → docs/architecture/setup.md §2b / ADR-033 / ADR-034

Split out of `test_agent_runtime.py` alongside `lumi/agent/warmup.py`. What is checked
here is not "does the wiring exist" but **what each engine turned out to be, and what
that means for whether Lumi is allowed out** — a different question, decided by a
different module.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import (  # noqa: F401  — `isolated_paths` / `no_ollama` are autouse fixtures
    FakeServer,
    FakeTts,
    conversation_is_possible,
    detects,
    installed_by_lumi,
    isolated_paths,
    make_coordinator,
    no_ollama,
)

from lumi.agent.warmup import warm_all, warm_stt, warm_tts
from lumi.providers.base import ProviderKind
from lumi.providers.registry import ProviderRegistry
from lumi.setup import coordinator as coordinator_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.state import EngineRuntime, SttSetupState


class TestEverythingIsWarmed:
    """★ **Whatever is left cold is a bill handed to the first reply.**

    docs/interfaces/provider.md（表 2d）. Observed 2026-08-18: the first answer took 7.5 s,
    of which 3767 ms was the LLM's weights, 3092 ms the TTS voice model, and 2489 ms the STT
    model — **and the STT one was invisible**, because `stt_ms` times `transcribe` while the
    model was built just outside it (it surfaced only as `unaccounted_ms`).
    """

    async def test_all_three_kinds_are_warmed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        conversation_is_possible(monkeypatch)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        registered = {
            kind: FakeTts(kind=kind)
            for kind in (ProviderKind.TTS, ProviderKind.LLM, ProviderKind.STT)
        }
        for provider in registered.values():
            providers.register(provider)

        await warm_all(providers, coordinator, "qwen3:8b")

        cold = [kind.value for kind, provider in registered.items() if not provider.is_loaded()]
        assert not cold, f"起動時に温めていない Provider がある: {cold}"
        assert coordinator.state.stt.runtime is EngineRuntime.READY
        assert server.stt_runtimes[-2:] == ["starting", "ready"]

    async def test_voice_input_starts_only_after_everything_is_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ The loading screen must never accept speech behind the character's back.

        **Moved by ADR-034.** The callback used to run straight after `warm_tts`, back when
        `ready` meant "the engine is up." Now it means all three work, and the microphone
        follows that same boundary — so by the time it opens, the LLM and STT are warm too.
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        conversation_is_possible(monkeypatch)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        registered = {
            kind: FakeTts(kind=kind)
            for kind in (ProviderKind.TTS, ProviderKind.LLM, ProviderKind.STT)
        }
        for provider in registered.values():
            providers.register(provider)

        callback_observation: list[tuple[str, bool, bool]] = []

        async def start_listening() -> None:
            callback_observation.append(
                (
                    server.boots[-1],
                    registered[ProviderKind.LLM].is_loaded(),
                    registered[ProviderKind.STT].is_loaded(),
                )
            )

        await warm_all(providers, coordinator, "qwen3:8b", on_ready=start_listening)

        assert callback_observation == [("ready", True, True)]

    async def test_no_tts_provider_never_opens_the_microphone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ **Setup is unfinished, so nothing listens** (ADR-034, setup.md §8 test 25).

        Lumi cannot speak, so the phase ends at `blocked`. Listening behind a screen that
        says Lumi has not started would take speech nobody could see being taken — and
        nothing could answer it anyway.
        """
        detects(monkeypatch, [])
        conversation_is_possible(monkeypatch)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        observed: list[str] = []

        async def on_ready() -> None:
            observed.append(server.boots[-1])

        await warm_all(ProviderRegistry(), coordinator, "qwen3:8b", on_ready=on_ready)

        assert observed == [], "セットアップ未完了なのにマイクを開いている"
        assert server.boots[-1] == "blocked"

    async def test_a_missing_speech_model_never_opens_the_microphone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**Every blocking component gates it, not just the TTS engine.**

        The engine starts fine here; there is simply nothing to hear with.
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        monkeypatch.setattr(coordinator_module, "is_model_installed", lambda *_: False)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        for kind in (ProviderKind.TTS, ProviderKind.LLM):
            providers.register(FakeTts(kind=kind))
        opened = False

        async def on_ready() -> None:
            nonlocal opened
            opened = True

        await warm_all(providers, coordinator, "qwen3:8b", on_ready=on_ready)

        assert not opened
        assert server.boots[-1] == "blocked"

    async def test_a_cold_stt_model_does_not_stop_startup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**Slow is not broken, and broken is not silent.** A model that won't build is a
        log line — whether it is *installed* was already settled by looking at the disk.
        """
        detects(monkeypatch, [])
        server = FakeServer()
        providers = ProviderRegistry()
        providers.register(FakeTts(fails=True, kind=ProviderKind.STT))
        coordinator = SetupCoordinator(server.as_server(), {})

        await warm_stt(providers, coordinator)  # **raises nothing**

        assert server.boots == []

    async def test_a_failed_stt_load_blocks_startup_and_skips_ready_callback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An installed model that cannot load is broken, not a successful warmup."""
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        conversation_is_possible(monkeypatch)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        providers.register(FakeTts())
        providers.register(FakeTts(kind=ProviderKind.LLM))
        providers.register(FakeTts(fails=True, kind=ProviderKind.STT))
        called = False

        async def on_ready() -> None:
            nonlocal called
            called = True

        await warm_all(providers, coordinator, "qwen3:8b", on_ready=on_ready)

        assert not called
        assert coordinator.state.stt.state is SttSetupState.INSTALLED
        assert coordinator.state.stt.runtime is EngineRuntime.FAILED
        assert coordinator.state.stt.reason is None
        assert server.stt_runtimes[-2:] == ["starting", "failed"]
        assert coordinator.boot.value == "blocked"


class TestWarmTts:
    async def test_reports_starting_then_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**The character has to come out.** Without the final report the Stage keeps
        showing the loading screen even though the engine is already able to speak.
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        # The engine has to be **the last thing missing** for its startup to be worth
        # showing as a wait at all (setup.md §2b).
        conversation_is_possible(monkeypatch)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        # Isolate the TTS transition: STT has its own runtime axis now (ADR-035).
        await coordinator.set_stt_runtime(EngineRuntime.READY)
        providers = ProviderRegistry()
        provider = FakeTts()
        providers.register(provider)

        await warm_tts(providers, coordinator)

        assert provider.load_calls == 1, "最初の発話まで起動を先送りしていない"
        assert coordinator.state.tts.runtime is EngineRuntime.READY
        assert server.boots[0] == "starting", "起動中であることを先に見せる"
        assert server.boots[-1] == "ready"

    async def test_does_not_show_a_wait_that_leads_nowhere(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ Regression (observed 2026-08-20): **declining the speech model still showed
        "starting AivisSpeech…" for two minutes, and only then said setup was incomplete.**

        Nothing about the engine coming up could have changed that outcome. The answer was
        already known when the user answered, and that is when they were still there to act
        on it.
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        monkeypatch.setattr(coordinator_module, "is_model_installed", lambda *_: False)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        providers.register(FakeTts())

        await warm_tts(providers, coordinator)

        assert "starting" not in server.boots, "揃わないと分かっているのに待たせている"
        assert set(server.boots) == {"blocked"}

    async def test_a_broken_engine_blocks_startup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ Installed but won't start = broken, and **broken does not read as started**
        (ADR-034). The old behaviour let the character out and left a warning line beside
        it, which is exactly how "could not start" got overwritten by "here I am."
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        conversation_is_possible(monkeypatch)
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        providers.register(FakeTts(fails=True))

        await warm_tts(providers, coordinator)

        assert coordinator.state.tts.runtime is EngineRuntime.FAILED
        assert server.boots[-1] == "blocked"

    async def test_does_not_claim_to_be_starting_what_it_cannot_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not set up, so no TTS Provider is registered. **`starting` is never broadcast**
        for something nothing is starting.
        """
        detects(monkeypatch, [])
        conversation_is_possible(monkeypatch)
        server = FakeServer()
        coordinator = await make_coordinator(server)

        await warm_tts(ProviderRegistry(), coordinator)

        assert coordinator.state.tts.runtime is EngineRuntime.STOPPED
        assert "starting" not in server.boots
        assert server.boots[-1] == "blocked"
