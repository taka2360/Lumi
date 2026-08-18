"""First-run setup's flow. **Uses neither WS nor HTTP** (the server is substituted).

Tests 2 / 11 from docs/architecture/setup.md §8, plus a regression test for a race
condition found in practice.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from lumi import paths as paths_module
from lumi.setup import coordinator as coordinator_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.detect import DetectedEngine
from lumi.setup.install import SetupError
from lumi.setup.state import EngineRuntime, LlmSetupState, SttSetupState, TtsSetupState
from lumi.transport.protocol import Result, Role
from lumi.transport.server import WsServer


class FakeServer:
    """Holds only the two methods of `WsServer` that the Coordinator uses."""

    def __init__(self, answers: list[str | None]) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.invocations: list[tuple[str, dict[str, Any]]] = []
        self._answers = answers

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        assert role is Role.STAGE
        self.notifications.append({"method": method, **(payload or {})})

    async def invoke(
        self,
        role: Role,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 0.0,  # noqa: ASYNC109 — to match WsServer's shape
    ) -> Result:
        del role, timeout
        self.invocations.append((method, payload or {}))
        if not self._answers:
            raise TimeoutError
        choice = self._answers.pop(0)
        return Result(corr_id="x", ok=True, payload={"choice": choice})

    def as_server(self) -> WsServer:
        return cast(WsServer, self)


@pytest.fixture(autouse=True)
def isolated_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths_module, "setup_state_file", lambda: tmp_path / "s.json")
    monkeypatch.setattr(paths_module, "engines_dir", lambda: tmp_path / "engines")
    # **Never reads the developer's real model directory.** Doing so makes the outcome
    # depend on whose machine the suite runs on
    monkeypatch.setattr(paths_module, "models_dir", lambda: tmp_path / "models")


@pytest.fixture(autouse=True)
def speech_model_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default for this file is **"the speech model is already there."**

    Otherwise every TTS test would also trip the STT question and have to account for
    it. The STT flow has its own tests below, which opt out of this.
    """
    monkeypatch.setattr(coordinator_module, "is_model_installed", lambda *_: True)


def no_speech_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coordinator_module, "is_model_installed", lambda *_: False)


def no_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    async def detect(_env: Any) -> list[DetectedEngine]:
        return []

    monkeypatch.setattr(coordinator_module, "detect_engines", detect)


def one_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def detect(_env: Any) -> list[DetectedEngine]:
        return [
            DetectedEngine(
                name="voicevox",
                display_name="VOICEVOX",
                port=50021,
                executable=Path("C:/x/run.exe"),
                running=False,
            )
        ]

    monkeypatch.setattr(coordinator_module, "detect_engines", detect)


def no_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    async def detect(_env: Any) -> DetectedEngine | None:
        return None

    monkeypatch.setattr(coordinator_module, "detect_ollama", detect)


def states_of(server: FakeServer, component: str = "tts") -> list[str]:
    return [
        item[component]["state"]
        for item in server.notifications
        if item["method"] == "stage.setup.state"
    ]


def boots_of(server: FakeServer) -> list[str]:
    """The sequence of broadcast boot phases. **The order the Stage actually sees**
    (docs/architecture/ui.md).
    """
    return [item["boot"] for item in server.notifications if item["method"] == "stage.setup.state"]


class TestDetection:
    async def test_reports_not_configured_on_a_clean_machine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        no_engines(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()

        assert coordinator.state.tts.state is TtsSetupState.NOT_CONFIGURED
        assert states_of(server) == ["not_configured"]

    async def test_uses_an_engine_the_user_already_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()

        assert coordinator.state.tts.state is TtsSetupState.DETECTED
        assert coordinator.state.tts.engine_name == "VOICEVOX"


class TestPrompt:
    async def test_asks_even_when_the_stage_connects_before_detection_finishes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**A race condition observed in practice.** Must ask even if the Stage connects before
        detection finishes.
        """
        started = asyncio.Event()

        async def slow_detect(_env: Any) -> list[DetectedEngine]:
            started.set()
            await asyncio.sleep(0.05)
            return []

        monkeypatch.setattr(coordinator_module, "detect_engines", slow_detect)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        initializing = asyncio.create_task(coordinator.initialize())
        await started.wait()
        # The Stage connects before detection finishes
        connected = asyncio.create_task(coordinator.on_stage_connected())
        await asyncio.gather(initializing, connected)

        assert [method for method, _ in server.invocations] == ["stage.setup.prompt"]

    async def test_skipping_keeps_lumi_running_and_visible(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Starts even when the choice is not to fetch. **State is explicitly reported as "not
        configured."**
        """
        no_engines(monkeypatch)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.tts.state is TtsSetupState.NOT_CONFIGURED
        assert (tmp_path / "s.json").is_file(), "答えたことが記録されていない"

    async def test_does_not_ask_twice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        no_engines(monkeypatch)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()
        await coordinator.on_stage_connected()

        assert len(server.invocations) == 1, "起動のたびに聞くのは鬱陶しさの典型"

    async def test_an_unanswered_prompt_is_not_recorded_as_answered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        no_engines(monkeypatch)
        server = FakeServer([])  # No answer comes back → TimeoutError
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert not (tmp_path / "s.json").exists()

    async def test_offers_one_retry_after_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        no_engines(monkeypatch)
        server = FakeServer(["install", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def failing_install(*_args: Any, **_kwargs: Any) -> Path:
            raise SetupError("network_unreachable")

        monkeypatch.setattr(coordinator_module, "install_engine", failing_install)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert len(server.invocations) == 2, "失敗したら一度は聞き直す"
        assert server.invocations[1][1]["retry"] is True
        assert server.invocations[1][1]["reason"] == "network_unreachable"
        # **A failure never reverts to "not yet attempted."**
        assert coordinator.state.tts.state is TtsSetupState.FAILED
        assert coordinator.state.tts.reason == "network_unreachable"
        assert states_of(server)[-1] == "failed"

    async def test_installs_when_the_user_asks_for_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        no_engines(monkeypatch)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def fake_install(*_args: Any, **_kwargs: Any) -> Path:
            return tmp_path / "engines" / "aivisspeech-1.2.0" / "run.exe"

        monkeypatch.setattr(coordinator_module, "install_engine", fake_install)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.tts.state is TtsSetupState.INSTALLED
        # Also broadcast when asking starts and when answering ends, so the same state repeats in a
        # row.
        assert states_of(server) == [
            "not_configured",
            "not_configured",
            "not_configured",
            "installing",
            "installed",
            "installed",
        ]
        # **The transition as seen by the Stage.** Progresses one-way: question → fetching → engine
        # starting. **Never returns to `setup` after `installing`** (returning would flash the
        # question screen). Right after fetching it's `starting`. **Marking it `ready` would show
        # the character only to pull it back.**
        assert boots_of(server) == [
            "ready",
            "ready",
            "setup",
            "installing",
            "starting",
            "starting",
        ]

    async def test_progress_keeps_the_installing_phase(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Progress during fetching is broadcast **while staying at `boot=installing`**.

        Progress broadcasts had been bypassing `_broadcast`, which confused
        `_prompting` (is this sequence in progress) with `_awaiting_answer` (is a
        question currently shown), streaming `boot=setup` for the entire fetch. The
        Stage falls back to "preparing…" for anything other than `installing`, so
        **no progress showed during the 200MB fetch.**
        """
        no_engines(monkeypatch)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def fake_install(*_args: Any, **kwargs: Any) -> Path:
            progress = kwargs["progress"]
            for fraction in (0.25, 0.5, 1.0):
                await progress(fraction)
            return tmp_path / "engines" / "aivisspeech-1.2.0" / "run.exe"

        monkeypatch.setattr(coordinator_module, "install_engine", fake_install)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        installing = [
            item
            for item in server.notifications
            if item["method"] == "stage.setup.state" and item["tts"]["state"] == "installing"
        ]
        assert [item["tts"]["progress"] for item in installing] == [0.0, 0.25, 0.5, 1.0]
        # **If even one broadcast returned to `setup`, the question screen would flash.**
        assert {item["boot"] for item in installing} == {"installing"}


class TestManagedEngine:
    async def test_an_engine_lumi_installed_reports_installed_not_detected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**"Lumi installed it" and "the user had already installed it" are different states**
        (setup.md §2).
        """

        async def detect(_env: Any) -> list[DetectedEngine]:
            return [
                DetectedEngine(
                    name="aivisspeech",
                    display_name="AivisSpeech",
                    port=10101,
                    executable=Path("C:/lumi/engines/aivisspeech-1.2.0/run.exe"),
                    running=False,
                    managed_by_lumi=True,
                    version="1.2.0",
                )
            ]

        monkeypatch.setattr(coordinator_module, "detect_engines", detect)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()

        assert coordinator.state.tts.state is TtsSetupState.INSTALLED
        assert coordinator.state.tts.version == "1.2.0"


class TestSpeechModel:
    """Fetching the speech-recognition model. **The same consent path as the engine**
    (docs/architecture/setup.md §2b).
    """

    async def test_asks_separately_from_the_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**Answering about the engine says nothing about the model.**

        Treating one answer as covering both would mean the second question is never
        asked — and Lumi would silently stay unable to listen.
        """
        no_engines(monkeypatch)
        no_speech_model(monkeypatch)
        server = FakeServer(["skip", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert [payload["component"] for _method, payload in server.invocations] == ["tts", "stt"]

    async def test_does_not_ask_when_the_model_is_already_there(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.invocations == []

    async def test_fetches_when_the_user_asks_for_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)
        fetched: list[str] = []

        async def fake_install(artifact: Any, models_dir: Path, *, progress: Any = None) -> Path:
            del models_dir
            fetched.append(artifact.name)
            await progress(1.0)
            return Path("C:/models/small")

        monkeypatch.setattr(coordinator_module, "install_stt_model", fake_install)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        # **Not hardcoded.** Which model ships is a decision that moves (ADR-027); that this
        # path fetches *the one the rest of Core will look for* is what must not move
        assert fetched == [coordinator_module.STT_ARTIFACT.name]
        assert coordinator.state.stt.state is SttSetupState.INSTALLED
        assert states_of(server, "stt")[-1] == "installed"

    async def test_declining_leaves_it_not_configured_and_never_asks_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**"Speaks but cannot listen" is a normal state**, and the question is not repeated."""
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()
        assert coordinator.state.stt.state is SttSetupState.NOT_CONFIGURED

        again = SetupCoordinator(server.as_server(), {})
        await again.initialize()
        await again.on_stage_connected()
        assert len(server.invocations) == 1, "起動のたびに聞き直さない"

    async def test_a_failed_fetch_never_reverts_to_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Tried and failed is not the same as never tried.** The reason is kept."""
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)

        async def fake_install(*_args: Any, **_kwargs: Any) -> Path:
            raise SetupError("hash_mismatch", "壊れている")

        monkeypatch.setattr(coordinator_module, "install_stt_model", fake_install)
        server = FakeServer(["install", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.stt.state is SttSetupState.FAILED
        assert coordinator.state.stt.reason == "hash_mismatch"

    async def test_fetching_the_model_never_disturbs_the_engine_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The three components are independent.** One update must not blank the others."""
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)

        async def fake_install(artifact: Any, models_dir: Path, *, progress: Any = None) -> Path:
            del artifact, models_dir, progress
            return Path("C:/models/small")

        monkeypatch.setattr(coordinator_module, "install_stt_model", fake_install)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        before = coordinator.state.tts
        await coordinator.on_stage_connected()

        assert coordinator.state.tts == before


class TestLlm:
    async def test_a_missing_ollama_is_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        no_ollama(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()

        assert coordinator.state.llm.state is LlmSetupState.NOT_CONFIGURED

    async def test_installed_but_not_running_is_detected_and_stopped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ **Never tells someone to install what they already have.**

        Over HTTP the two are indistinguishable, which is why detection settles it
        (docs/architecture/setup.md §2b).
        """
        one_engine(monkeypatch)

        async def detect(_env: Any) -> DetectedEngine | None:
            return DetectedEngine(
                name="ollama",
                display_name="Ollama",
                port=11434,
                executable=Path("C:/ollama.exe"),
                running=False,
            )

        monkeypatch.setattr(coordinator_module, "detect_ollama", detect)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()

        assert coordinator.state.llm.state is LlmSetupState.DETECTED
        assert coordinator.state.llm.runtime is EngineRuntime.STOPPED

    async def test_a_missing_model_is_reported_by_whoever_found_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Detection cannot see this.** Only the Provider talks to Ollama's API."""
        one_engine(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})
        await coordinator.initialize()

        await coordinator.report_llm(
            LlmSetupState.MODEL_MISSING, reason="ollama pull qwen3.5:9b", model="qwen3.5:9b"
        )

        assert coordinator.state.llm.state is LlmSetupState.MODEL_MISSING
        assert coordinator.state.llm.model == "qwen3.5:9b"
        assert states_of(server, "llm")[-1] == "model_missing"

    async def test_never_holds_up_the_character(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**Lumi neither fetches nor starts Ollama**, so waiting accomplishes nothing."""
        one_engine(monkeypatch)
        no_ollama(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.set_runtime(EngineRuntime.READY)

        assert boots_of(server)[-1] == "ready"
