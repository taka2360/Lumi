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
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import apsw
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
            assert foreground.state is ActivityState.RUNNING, (
                "idle must be created with running state"
            )
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

            async def shutdown(self) -> None:
                """Shutdown stops the turns the loop spawned, before the databases close."""
                timeline.append("reactive.shutdown")

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
            assert not audio.started, "AudioIO opened before boot: ready notification completed"

            server.release_ready.set()
            await audio.started_event.wait()
            await loop.started_event.wait()

            assert timeline == ["audio.start", "reactive.run"]
            assert server.boots[-1] == "ready"
        finally:
            server.release_ready.set()
            await runtime.stop()

        # **Stopping the runtime tears the loop down, it does not just drop it.** Cancelling
        # the loop task leaves the turns it spawned running, and one of those writing an
        # episode into a database that has already closed loses the last thing that was said
        assert timeline == ["audio.start", "reactive.run", "reactive.shutdown"]

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

        try:
            await runtime._warm_llm_after_detection()

            assert calls == 2
        finally:
            await runtime.stop()

    async def test_listening_stays_false_when_audio_start_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        detects(monkeypatch, [])
        server = FakeServer()
        runtime = ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )

        class BrokenAudio:
            async def start(self) -> None:
                raise OSError("capture unavailable")

            async def stop(self) -> None:
                pass

        runtime._audio = cast(Any, BrokenAudio())
        try:
            with pytest.raises(OSError, match="capture unavailable"):
                await runtime._start_listening()
            assert not runtime._listening
        finally:
            await runtime.stop()

    async def test_model_selection_unloads_the_previous_llm_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        detects(monkeypatch, [])
        server = FakeServer()
        runtime = ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )

        class TrackedLlm(FakeTts):
            def __init__(self) -> None:
                super().__init__(kind=ProviderKind.LLM)
                self.unloaded = False

            async def unload(self) -> None:
                self.unloaded = True
                await super().unload()

        previous = TrackedLlm()
        runtime._model = "old"
        runtime._providers.register(previous)
        try:
            await runtime._select_llm_model("new")

            assert previous.unloaded
            assert runtime._providers.peek(ProviderKind.LLM).id == "ollama:new"
        finally:
            await runtime.stop()


class TestStartupMaintenance:
    """**Housekeeping must not be able to stop Lumi from starting.**

    Retention and the fade sweep both run at startup, before a session can begin. Neither
    is what the user opened the app for, and **refusing to start because old records could
    not be tidied would trade the whole product for a chore.**
    """

    async def _runtime(self, monkeypatch: pytest.MonkeyPatch) -> ConversationRuntime:
        detects(monkeypatch, [])
        server = FakeServer()
        return ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )

    async def test_startup_survives_a_failing_retention_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime = await self._runtime(monkeypatch)

        class Broken:
            async def run(self, *_args: Any, **_kwargs: Any) -> list[Any]:
                raise RuntimeError("the disk is on fire")

        runtime._retention = cast(Any, Broken())
        try:
            await runtime.start()

            assert runtime.arbiter.current().kind is ActivityKind.IDLE
        finally:
            await runtime.stop()

    async def test_startup_survives_a_failing_fade_sweep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ Failing to forget is a stale memory. **Failing to start is no Lumi at all.**"""
        runtime = await self._runtime(monkeypatch)

        class Broken:
            async def archive_faded(self, **_kwargs: Any) -> list[str]:
                raise RuntimeError("the disk is on fire")

        runtime._memories = cast(Any, Broken())
        try:
            await runtime.start()

            assert runtime.arbiter.current().kind is ActivityKind.IDLE
        finally:
            await runtime.stop()

    async def test_the_sweep_runs_before_a_session_can_begin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decay is a function of elapsed time, so the sweep is what "faded while Lumi was
        off" means. **It has to happen before the first turn can retrieve anything**, and
        the Arbiter starting is the moment a turn becomes possible.
        """
        runtime = await self._runtime(monkeypatch)
        swept_before_start: list[bool] = []
        real = runtime._memories

        class Watched:
            async def archive_faded(self, **kwargs: Any) -> Any:
                try:
                    runtime.arbiter.current()
                except RuntimeError:
                    swept_before_start.append(True)
                else:
                    swept_before_start.append(False)
                return await real.archive_faded(**kwargs)

        runtime._memories = cast(Any, Watched())
        try:
            await runtime.start()

            assert swept_before_start == [True]
        finally:
            await runtime.stop()


class TestReflection:
    """**Memories are made while nobody is talking** (docs/architecture/memory.md §4).

    The trigger is an idle period rather than a session end, and the tests drive it with
    an injected clock: a test that waited five real minutes would be a test nobody runs.
    """

    async def _runtime(self, monkeypatch: pytest.MonkeyPatch) -> ConversationRuntime:
        detects(monkeypatch, [])
        server = FakeServer()
        return ConversationRuntime(
            server.as_server(),
            await make_coordinator(server),
            AudioPlan(capture=None, playback=None, warnings=()),
        )

    async def test_a_quiet_lumi_reflects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """★ The idle pass covers the session that just ended too: at the next start,
        **"no turn yet" is already an idle period**, so an unreflected transcript is picked
        up without holding shutdown open for an inference.
        """
        runtime = await self._runtime(monkeypatch)
        ran = asyncio.Event()

        class Reflecting:
            def __init__(self, **_kwargs: Any) -> None: ...

            async def run(self) -> Any:
                ran.set()
                return type("Report", (), {"learned": 0})()

        monkeypatch.setattr(runtime_module, "ReflectionJob", Reflecting)
        monkeypatch.setattr(runtime_module, "REFLECTION_CHECK_SECONDS", 0.001)
        monkeypatch.setattr(runtime_module, "REFLECTION_IDLE_AFTER", timedelta(0))
        # **Registered through the same seam startup uses.** Registering directly would be
        # overwritten by `_register_providers`, which is exactly what happened first.
        monkeypatch.setattr(
            runtime_module, "OllamaProvider", lambda _model: FakeTts(kind=ProviderKind.LLM)
        )
        try:
            await runtime.start()

            async with asyncio.timeout(2):
                await ran.wait()
        finally:
            await runtime.stop()

    async def test_a_lumi_in_the_middle_of_a_conversation_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ **A pause for breath is not the end of a conversation.** Reflecting mid-turn
        would put a second inference in front of the reply — the thing the lease exists to
        prevent, arriving one step earlier.
        """
        runtime = await self._runtime(monkeypatch)
        started: list[str] = []

        class Reflecting:
            def __init__(self, **_kwargs: Any) -> None: ...

            async def run(self) -> Any:
                started.append("ran")
                return type("Report", (), {"learned": 0})()

        monkeypatch.setattr(runtime_module, "ReflectionJob", Reflecting)
        monkeypatch.setattr(runtime_module, "REFLECTION_CHECK_SECONDS", 0.001)
        monkeypatch.setattr(runtime_module, "REFLECTION_IDLE_AFTER", timedelta(minutes=5))
        try:
            await runtime.start()
            await asyncio.sleep(0.05)

            assert started == []
        finally:
            await runtime.stop()

    async def test_being_asked_to_remember_shortens_the_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ 「覚えておいて」 is answered while it still means something.

        **It does not skip the wait, it shortens it.** Reflection takes an inference lease,
        so starting one the instant the phrase is heard would put the extraction in
        contention with the reply to the sentence that asked for it.
        """
        runtime = await self._runtime(monkeypatch)
        ran = asyncio.Event()

        class Reflecting:
            def __init__(self, **_kwargs: Any) -> None: ...

            async def run(self) -> Any:
                ran.set()
                return type("Report", (), {"learned": 0})()

        monkeypatch.setattr(runtime_module, "ReflectionJob", Reflecting)
        monkeypatch.setattr(runtime_module, "REFLECTION_CHECK_SECONDS", 0.001)
        # Long enough that the ordinary trigger cannot be what fires.
        monkeypatch.setattr(runtime_module, "REFLECTION_IDLE_AFTER", timedelta(days=1))
        monkeypatch.setattr(runtime_module, "REFLECTION_ASKED_IDLE_AFTER", timedelta(0))
        monkeypatch.setattr(
            runtime_module, "OllamaProvider", lambda _model: FakeTts(kind=ProviderKind.LLM)
        )
        try:
            await runtime.start()
            assert runtime._loop is not None
            await asyncio.sleep(0.02)
            assert not ran.is_set()

            runtime._loop._asked_to_remember = True

            async with asyncio.timeout(2):
                await ran.wait()
        finally:
            await runtime.stop()

    async def test_an_engine_that_is_not_up_is_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The LLM is warmed in the background; the first idle pass can easily land before
        it. **The next one tries again**, and nothing was lost in between.
        """
        runtime = await self._runtime(monkeypatch)
        monkeypatch.setattr(runtime_module, "REFLECTION_CHECK_SECONDS", 0.001)
        monkeypatch.setattr(runtime_module, "REFLECTION_IDLE_AFTER", timedelta(0))
        try:
            await runtime.start()
            await asyncio.sleep(0.05)

            assert runtime._reflecting is not None
            assert not runtime._reflecting.done()
        finally:
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

        # The DB handles are the last thing `stop()` releases: closed means it ran to the
        # end. **All three**, because closing one and leaking two is the same bug
        for database in (runtime._memory_db, runtime._audit_db, runtime._events_db):
            with pytest.raises(apsw.ConnectionClosedError):
                database._conn.execute("SELECT 1")


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
