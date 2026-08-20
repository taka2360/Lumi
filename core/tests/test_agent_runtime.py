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

from lumi import paths as paths_module
from lumi.agent import runtime as runtime_module
from lumi.agent.runtime import ConversationRuntime, _warm, warm_stt, warm_tts
from lumi.audio.devices import AudioPlan
from lumi.kernel.activity import ActivityKind, ActivityState
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderKind,
    ProviderUnavailable,
    ResourceHint,
    UnloadPolicy,
)
from lumi.providers.registry import ProviderRegistry
from lumi.setup import coordinator as coordinator_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.detect import DetectedEngine
from lumi.setup.state import EngineRuntime
from lumi.transport.protocol import Role
from lumi.transport.server import RequestRefused, WsServer


class FakeServer:
    """Holds only what the Coordinator and the runtime touch."""

    def __init__(self) -> None:
        self.boots: list[str] = []
        #: Inbound routes the runtime registered. **The allowlist is this registry** (ADR-028)
        self.inbound: dict[str, Any] = {}
        #: Every settings payload broadcast. **Changing one has to reach everybody**
        self.settings: list[dict[str, Any]] = []

    def on_request(self, method: str, handler: Any) -> None:
        self.inbound[method] = handler

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        assert role is Role.STAGE
        if method == "stage.setup.state" and payload is not None:
            self.boots.append(str(payload["boot"]))
        if method == "stage.settings.state" and payload is not None:
            self.settings.append(payload)

    def as_server(self) -> WsServer:
        return cast(WsServer, self)


class ReadyGateServer(FakeServer):
    """Pauses the ready notification so startup ordering can be observed."""

    def __init__(self) -> None:
        super().__init__()
        self.ready_seen = asyncio.Event()
        self.release_ready = asyncio.Event()

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        await super().notify(role, method, payload)
        if method == "stage.setup.state" and payload is not None and payload["boot"] == "ready":
            self.ready_seen.set()
            await self.release_ready.wait()


class FakeTts:
    """Holds only what `ProviderRegistry` touches. **Never starts a process.**"""

    id = "fake-tts"
    kind = ProviderKind.TTS

    def __init__(self, *, fails: bool = False, kind: ProviderKind = ProviderKind.TTS) -> None:
        self.kind = kind
        self.id = f"fake-{kind.value}"
        self._fails = fails
        self._loaded = False
        self.load_calls = 0

    async def load(self) -> None:
        self.load_calls += 1
        if self._fails:
            raise ProviderUnavailable("engine_not_ready", "Engine runtime status: failed")
        self._loaded = True

    async def unload(self) -> None:
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def resource_hint(self) -> ResourceHint:
        return ResourceHint(
            device_pref=DevicePref.EXTERNAL_PROCESS,
            vram_estimate_mb=0,
            load_time_estimate_ms=0,
            unload_policy=UnloadPolicy.PINNED,
        )

    def attribution(self) -> Attribution:
        return Attribution(display_name="Fake", credit_text="Fake", license_name="MIT")


@pytest.fixture(autouse=True)
def isolated_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_module, "setup_state_file", lambda: tmp_path / "s.json")
    content_dir = tmp_path / "content"
    char_dir = content_dir / "characters" / "lumi"
    char_dir.mkdir(parents=True)
    real_char_dir = paths_module.default_character_dir()
    if real_char_dir.is_dir():
        (char_dir / "character.toml").write_text(
            (real_char_dir / "character.toml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (char_dir / "voice.toml").write_text(
            (real_char_dir / "voice.toml").read_text(encoding="utf-8"), encoding="utf-8"
        )
    (char_dir / "model.vrm").write_bytes(b"glTF ")
    monkeypatch.setattr(paths_module, "default_character_dir", lambda: char_dir)


def detects(monkeypatch: pytest.MonkeyPatch, engines: list[DetectedEngine]) -> None:
    async def detect(_env: Any) -> list[DetectedEngine]:
        return engines

    monkeypatch.setattr(coordinator_module, "detect_engines", detect)


def installed_by_lumi(tmp_path: Path) -> DetectedEngine:
    return DetectedEngine(
        name="aivisspeech",
        display_name="AivisSpeech",
        port=10101,
        executable=tmp_path / "run.exe",
        running=False,
        managed_by_lumi=True,
        version="1.2.0",
    )


async def make_coordinator(server: FakeServer) -> SetupCoordinator:
    coordinator = SetupCoordinator(server.as_server(), {})
    await coordinator.initialize()
    return coordinator


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
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        registered = {
            kind: FakeTts(kind=kind)
            for kind in (ProviderKind.TTS, ProviderKind.LLM, ProviderKind.STT)
        }
        for provider in registered.values():
            providers.register(provider)

        await _warm(providers, coordinator, "qwen3:8b")

        cold = [kind.value for kind, provider in registered.items() if not provider.is_loaded()]
        assert not cold, f"起動時に温めていない Provider がある: {cold}"

    async def test_voice_input_starts_only_after_loading_is_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ The loading screen must never accept speech behind the character's back.

        `warm_tts` owns the `starting` → `ready` transition, so the callback that opens
        AudioIO must run after that broadcast and before the remaining warmups.
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
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

        await _warm(providers, coordinator, "qwen3:8b", on_ready=start_listening)

        assert callback_observation == [("ready", False, False)]

    async def test_no_tts_provider_still_publishes_ready_before_callback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        detects(monkeypatch, [])
        server = FakeServer()
        coordinator = await make_coordinator(server)
        initial_notifications = len(server.boots)
        observed: list[tuple[int, str]] = []

        async def on_ready() -> None:
            observed.append((len(server.boots), server.boots[-1]))

        await _warm(ProviderRegistry(), coordinator, "qwen3:8b", on_ready=on_ready)

        # `_warm` reports LLM state after the callback as well. The callback must observe
        # the additional ready notification emitted by the no-TTS branch first.
        assert observed == [(initial_notifications + 1, "ready")]
        assert server.boots[initial_notifications] == "ready"

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

        await warm_stt(providers)  # **raises nothing**

        assert server.boots == []


class TestWarmTts:
    async def test_reports_starting_then_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**The character has to come out.** Without the final report the Stage keeps
        showing the loading screen even though the engine is already able to speak.
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        provider = FakeTts()
        providers.register(provider)

        await warm_tts(providers, coordinator)

        assert provider.load_calls == 1, "最初の発話まで起動を先送りしていない"
        assert coordinator.state.tts.runtime is EngineRuntime.READY
        assert server.boots[0] == "starting", "起動中であることを先に見せる"
        assert server.boots[-1] == "ready"

    async def test_a_broken_engine_still_lets_the_character_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Installed but won't start = broken. **The character is never held hostage**
        (docs/architecture/ui.md) — the SetupPanel is what says it's broken.
        """
        detects(monkeypatch, [installed_by_lumi(tmp_path)])
        server = FakeServer()
        coordinator = await make_coordinator(server)
        providers = ProviderRegistry()
        providers.register(FakeTts(fails=True))

        await warm_tts(providers, coordinator)

        assert coordinator.state.tts.runtime is EngineRuntime.FAILED
        assert server.boots[-1] == "ready"

    async def test_does_not_claim_to_be_starting_what_it_cannot_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not set up, so no TTS Provider is registered. **`starting` is never broadcast**
        for something nothing is starting.
        """
        detects(monkeypatch, [])
        server = FakeServer()
        coordinator = await make_coordinator(server)

        await warm_tts(ProviderRegistry(), coordinator)

        assert coordinator.state.tts.runtime is EngineRuntime.STOPPED
        assert server.boots[-1] == "ready"


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
        monkeypatch.setattr(runtime_module, "_warm", explodes)
        server = FakeServer()
        runtime = ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )
        await runtime.start()
        # Let the warmup task run and fail before the cancel lands on an already-done task
        await asyncio.sleep(0)
        await asyncio.sleep(0)

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
