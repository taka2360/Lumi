"""Startup wiring. **Neither WS nor an engine process is involved** (both are substituted).

**This file exists because the unit tests all build their own wiring.** Every collaborator
here is exercised elsewhere; what is only testable at this level is *whether they were
connected at all*. Both regressions below were invisible to every other test:

* nobody started the engine, so the Stage showed "starting" forever (observed 2026-08-17)
* nobody called `arbiter.start()`, so Lumi went deaf on the first utterance
  (observed 2026-08-17)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from fakes import (  # noqa: F401  — `isolated_paths` / `no_ollama` are autouse fixtures
    FakeServer,
    FakeTts,
    ReadyGateServer,
    conversation_is_possible,
    detects,
    installed_by_lumi,
    isolated_paths,
    make_coordinator,
    no_ollama,
)

from lumi import paths as paths_module
from lumi.agent import runtime as runtime_module
from lumi.agent.runtime import ConversationRuntime
from lumi.audio.devices import AudioPlan
from lumi.kernel.activity import ActivityKind, ActivityState
from lumi.providers.base import (
    ProviderKind,
)
from lumi.setup.state import BootPhase, EngineRuntime, LlmSetup, LlmSetupState, SetupSnapshot
from lumi.transport.server import RequestRefused


class TestAssembly:
    """**Does starting the runtime actually leave a usable system behind.**"""

    async def test_the_arbiter_is_started(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ Regression (observed 2026-08-17): **Lumi answered nothing when spoken to.**

        `current()` raises while `_foreground` is unset, so the first `SPEECH_STARTED`
        killed the reactive loop — and asyncio only reports an unretrieved task exception
        at GC, so **nothing said anything was wrong.**

        Startup sequence step 9 (docs/architecture/core.md §7) says the idle Activity is
        created `running` at startup. **Every other test built its own Arbiter and started
        it**, so the production path was the one place this never happened.
        """
        detects(monkeypatch, [])
        server = FakeServer()
        runtime = ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )
        try:
            await runtime.start()

            foreground = runtime.arbiter.current()
            assert foreground.kind is ActivityKind.IDLE
            assert foreground.state is ActivityState.RUNNING, "idle は running で生成される"
        finally:
            await runtime.stop()

    async def test_start_wires_audio_and_reactive_loop_after_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        server = ReadyGateServer()
        timeline: list[str] = []

        class FakeAudio:
            def __init__(self, _plan: AudioPlan) -> None:
                self.started = False
                self.started_event = asyncio.Event()

            @property
            def can_listen(self) -> bool:
                return True

            async def start(self) -> None:
                timeline.append("audio.start")
                self.started = True
                self.started_event.set()

            async def stop(self) -> None:
                self.started = False

        class FakeLoop:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                self.started_event = asyncio.Event()
                self._stop = asyncio.Event()

            async def run(self) -> None:
                timeline.append("reactive.run")
                self.started_event.set()
                await self._stop.wait()

        monkeypatch.setattr(runtime_module, "AudioIO", FakeAudio)
        monkeypatch.setattr(runtime_module, "ReactiveLoop", FakeLoop)
        monkeypatch.setattr(
            runtime_module,
            "AivisSpeechProvider",
            lambda *_args, **_kwargs: FakeTts(),
        )
        monkeypatch.setattr(
            runtime_module,
            "OllamaProvider",
            lambda _model: FakeTts(kind=ProviderKind.LLM),
        )
        monkeypatch.setattr(
            runtime_module,
            "FasterWhisperProvider",
            lambda _model, _root: FakeTts(kind=ProviderKind.STT),
        )
        # **`ready` now means all three work** (ADR-034), so the speech model has to be
        # there for the microphone to open at all.
        conversation_is_possible(monkeypatch)

        coordinator = await make_coordinator(server)
        runtime = ConversationRuntime(
            server.as_server(),
            coordinator,
            AudioPlan(capture=None, playback=None, warnings=()),
        )
        audio = cast(FakeAudio, runtime._audio)
        loop = cast(FakeLoop, runtime._loop)
        try:
            await runtime.start()

            await server.ready_seen.wait()
            assert not audio.started, "boot: ready の通知完了前に AudioIO を開いている"

            server.release_ready.set()
            await audio.started_event.wait()
            await loop.started_event.wait()

            assert timeline == ["audio.start", "reactive.run"]
            assert server.boots[-1] == "ready"
        finally:
            server.release_ready.set()
            await runtime.stop()

    async def test_a_successful_model_pull_is_warmed_again_before_continuing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The prompt/pull can finish inside the current warm-up task, so it must loop."""
        detects(monkeypatch, [])
        server = FakeServer()
        runtime = ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )

        class PullingSetup:
            boot = BootPhase.BLOCKED
            state = SetupSnapshot()

        setup = PullingSetup()
        runtime._setup = cast(Any, setup)
        calls = 0

        async def model_warmup(*_args: Any) -> None:
            nonlocal calls
            calls += 1
            setup.state = SetupSnapshot(
                llm=LlmSetup(
                    state=LlmSetupState.DETECTED,
                    runtime=EngineRuntime.STARTING if calls == 1 else EngineRuntime.READY,
                    reason="model_checking" if calls == 1 else None,
                )
            )

        monkeypatch.setattr(runtime_module, "warm_llm", model_warmup)

        await runtime._warm_llm_after_detection()

        assert calls == 2
        await runtime.stop()


class TestShutdown:
    """**Stopping has to finish.** Everything after the task cancels releases something."""

    async def test_a_crashed_warmup_does_not_abort_the_rest_of_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ Regression: **an unexpected warmup failure was re-raised out of `stop()`.**

        The task is never awaited while it runs, so its exception surfaced only when
        `stop()` awaited the cancelled task — and from there it skipped the audio device,
        the engine process and the DB handle, all of which `stop()` is the one to release.
        """

        async def explodes(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Unexpected failure")

        detects(monkeypatch, [])
        # **Patched on `runtime`, not on `warmup`.** `runtime` imported the name, so
        # rebinding it there is what the running code actually calls.
        monkeypatch.setattr(runtime_module, "warm_all", explodes)
        server = FakeServer()
        runtime = ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )
        await runtime.start()
        # The cancel has to land on an already-done task, so the failure must happen first
        warmup = runtime._warmup
        assert warmup is not None
        with pytest.raises(RuntimeError, match="Unexpected failure"):
            await warmup

        await runtime.stop()  # **raises nothing**

        # The DB handle is the last thing `stop()` releases: closed means it ran to the end
        with pytest.raises(sqlite3.ProgrammingError):
            runtime._database._conn.execute("SELECT 1")


class TestSettingsUpdate:
    """The Stage → Core direction, at the runtime level. **ADR-028.**

    What is protected here is that **Core decides**: it validates, it may refuse, and the
    Stage never writes anything itself.
    """

    async def build(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
        detects(monkeypatch, [])
        monkeypatch.setattr(paths_module, "settings_file", lambda: tmp_path / "settings.json")
        server = FakeServer()
        runtime = ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )
        return runtime, server

    async def test_the_route_is_registered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ **The allowlist is the registry** (ADR-028). Unregistered means unreachable."""
        _runtime, server = await self.build(monkeypatch, tmp_path)
        assert "stage.settings.update" in server.inbound

    async def test_a_change_is_written_and_broadcast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runtime, server = await self.build(monkeypatch, tmp_path)
        handler = server.inbound["stage.settings.update"]

        answer = await handler({"changes": {"llm_model": "gemma3:12b"}})

        assert answer == {"applied_at_next_start": True}
        stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert stored["llm_model"] == "gemma3:12b"
        # **Everyone sees the new state**, not just whoever asked
        assert server.settings[-1]["values"]["llm_model"]["value"] == "gemma3:12b"
        del runtime

    async def test_locale_is_marked_as_applied_immediately(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runtime, server = await self.build(monkeypatch, tmp_path)
        answer = await server.inbound["stage.settings.update"]({"changes": {"locale": "en"}})

        assert answer == {"applied_at_next_start": False}
        assert server.settings[-1]["values"]["locale"]["value"] == "en"
        del runtime

    async def test_tts_speed_is_marked_as_applied_immediately(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runtime, server = await self.build(monkeypatch, tmp_path)
        received: list[float] = []

        class RecordingLoop:
            def set_tts_speed(self, speed: float) -> None:
                received.append(speed)

        runtime._loop = cast(Any, RecordingLoop())
        answer = await server.inbound["stage.settings.update"]({"changes": {"tts_speed": "1.4"}})

        assert answer == {"applied_at_next_start": False}
        assert server.settings[-1]["values"]["tts_speed"]["value"] == "1.4"
        assert received == [1.4]
        del runtime

    async def test_a_key_that_is_not_a_setting_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ **fail-closed.** The Stage asking for something is not a reason to write it."""
        _runtime, server = await self.build(monkeypatch, tmp_path)

        with pytest.raises(RequestRefused, match="UnknownSetting"):
            await server.inbound["stage.settings.update"]({"changes": {"shell_command": "rm"}})

    async def test_a_malformed_payload_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**The transport does not validate payloads** (ADR-028) — the handler does."""
        _runtime, server = await self.build(monkeypatch, tmp_path)
        handler = server.inbound["stage.settings.update"]

        for payload in ({}, {"changes": "all of them"}, {"changes": {"llm_model": 42}}):
            with pytest.raises(RequestRefused, match="invalid_payload"):
                await handler(payload)

    async def test_an_unreadable_file_is_never_overwritten(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ **The user's own configuration outranks one update.** It stays fixable by hand."""
        broken = tmp_path / "settings.json"
        broken.write_text("{ broken", encoding="utf-8")
        _runtime, server = await self.build(monkeypatch, tmp_path)

        with pytest.raises(RequestRefused, match="SettingsUnreadable"):
            await server.inbound["stage.settings.update"]({"changes": {"llm_model": "x"}})

        assert broken.read_text(encoding="utf-8") == "{ broken"
