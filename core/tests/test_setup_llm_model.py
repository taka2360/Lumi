"""Detecting Ollama, choosing a model, and pulling it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from setup_harness import (  # noqa: F401  — the fixtures here are autouse
    FakeServer,
    _local_models,
    boots_of,
    isolated_paths,
    missing_models,
    no_engines,
    no_ollama,
    no_real_ollama,
    no_speech_model,
    ollama_present,
    one_engine,
    snapshots_of,
    speech_model_present,
    states_of,
)

from lumi import settings as settings_module
from lumi.providers.base import EngineRuntime
from lumi.setup import detection as detection_module
from lumi.setup import llm_model as llm_model_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.detect import DetectedEngine
from lumi.setup.ollama import OllamaLocalModel
from lumi.setup.state import (
    BootPhase,
    LlmSetup,
    LlmSetupState,
)
from lumi.transport.methods import (
    METHOD_SETUP_RECHECK_OLLAMA,
)


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

    async def test_recheck_detects_running_ollama_and_starts_model_confirmation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        detections: list[DetectedEngine | None] = [
            None,
            DetectedEngine(
                name="ollama",
                display_name="Ollama",
                port=11434,
                executable=Path("C:/ollama.exe"),
                running=True,
            ),
        ]

        async def detect(_env: Any) -> DetectedEngine | None:
            return detections.pop(0)

        monkeypatch.setattr(detection_module, "detect_ollama", detect)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})
        model_checks: list[None] = []
        coordinator.set_ollama_detected_handler(lambda: model_checks.append(None))
        await coordinator.initialize()

        result = await server.request_handlers[METHOD_SETUP_RECHECK_OLLAMA]({})

        assert result == {"detected": True, "running": True}
        assert coordinator.state.llm.state is LlmSetupState.DETECTED
        assert coordinator.state.llm.runtime is EngineRuntime.STARTING
        assert model_checks == [None]

    async def test_recheck_does_not_claim_an_installed_but_stopped_ollama_is_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)

        async def detect(_env: Any) -> DetectedEngine | None:
            return DetectedEngine(
                name="ollama",
                display_name="Ollama",
                port=11434,
                executable=Path("C:/ollama.exe"),
                running=False,
            )

        monkeypatch.setattr(detection_module, "detect_ollama", detect)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})
        await coordinator.initialize()

        result = await server.request_handlers[METHOD_SETUP_RECHECK_OLLAMA]({})

        assert result == {"detected": True, "running": False, "starting": True}
        assert coordinator.state.llm.runtime is EngineRuntime.STARTING

    async def test_recheck_cannot_downgrade_an_already_ready_ollama(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})
        await coordinator.initialize()
        before = coordinator.state.llm

        result = await server.request_handlers[METHOD_SETUP_RECHECK_OLLAMA]({})

        assert result == {"detected": True, "running": True}
        assert coordinator.state.llm == before

    async def test_installed_but_not_running_gets_a_startup_grace_period(
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

        monkeypatch.setattr(detection_module, "detect_ollama", detect)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()

        assert coordinator.state.llm.state is LlmSetupState.DETECTED
        assert coordinator.state.llm.runtime is EngineRuntime.STARTING
        assert coordinator.state.llm.reason == "ollama_starting"

    async def test_startup_grace_expires_before_asking_the_user_to_start_ollama(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        now = 100.0

        async def detect(_env: Any) -> DetectedEngine | None:
            return DetectedEngine(
                name="ollama",
                display_name="Ollama",
                port=11434,
                executable=Path("C:/ollama.exe"),
                running=False,
            )

        monkeypatch.setattr(detection_module, "detect_ollama", detect)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {}, clock=lambda: now)
        await coordinator.initialize()
        now = 116.0

        result = await server.request_handlers[METHOD_SETUP_RECHECK_OLLAMA]({})

        assert result == {"detected": True, "running": False, "starting": False}
        assert coordinator.state.llm.runtime is EngineRuntime.STOPPED
        assert coordinator.state.llm.reason == "ollama_not_running"

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

    async def test_model_pull_requires_a_size_labelled_user_choice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        pulled: list[str] = []
        selected: list[str] = []
        warmups: list[None] = []

        async def pull(artifact: Any, *, progress: Any = None) -> None:
            pulled.append(artifact.name)
            if progress is not None:
                await progress(1_000, 1_000)
                await progress(3_300_000_000, 6_600_000_000)

        monkeypatch.setattr(llm_model_module, "pull_ollama_model", pull)
        server = FakeServer([{"choice": "install", "model": "qwen3.5:9b"}])
        coordinator = SetupCoordinator(server.as_server(), {})
        coordinator.set_ollama_detected_handler(lambda: warmups.append(None))

        async def select(model: str) -> None:
            selected.append(model)

        coordinator.set_llm_model_selected_handler(select)
        await coordinator.initialize()

        await coordinator.report_llm(
            LlmSetupState.MODEL_MISSING,
            reason="model_missing",
            model="qwen3.5:9b",
        )

        prompt = next(
            payload for method, payload in server.invocations if method == "stage.setup.prompt"
        )
        assert prompt["component"] == "llm_model"
        assert prompt["model"] == {
            "model": "qwen3.5:9b",
            "display_name": "Qwen 3.5 9B",
            "size_bytes": 6_600_000_000,
            "installed": False,
        }
        assert pulled == ["qwen3.5:9b"]
        assert selected == ["qwen3.5:9b"]
        assert any(
            notification.get("llm", {}).get("progress") == 0.5
            for notification in server.notifications
        )
        assert coordinator.state.llm.state is LlmSetupState.DETECTED
        assert coordinator.state.llm.reason == "model_checking"
        assert warmups == [None]

    async def test_recheck_preserves_model_missing_while_model_prompt_is_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})
        await coordinator.initialize()
        await coordinator._state.replace(
            llm=LlmSetup(
                state=LlmSetupState.MODEL_MISSING,
                model="qwen3.5:9b",
                runtime=EngineRuntime.READY,
            )
        )
        coordinator._llm_model._prompting = True

        result = await coordinator._recheck_ollama({})

        assert result == {"detected": True, "running": True}
        assert coordinator.state.llm.state is LlmSetupState.MODEL_MISSING

    async def test_model_pull_progress_restarts_for_a_same_sized_layer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        progress_values: list[float] = []

        async def pull(_artifact: Any, *, progress: Any = None) -> None:
            assert progress is not None
            for completed in (100, 500, 10, 100):
                await progress(completed, 1_000)

        monkeypatch.setattr(llm_model_module, "pull_ollama_model", pull)
        server = FakeServer([{"choice": "install", "model": "qwen3.5:9b"}])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def select(_model: str) -> None:
            pass

        coordinator.set_llm_model_selected_handler(select)
        await coordinator.initialize()
        await coordinator.report_llm(
            LlmSetupState.MODEL_MISSING,
            reason="model_missing",
            model="qwen3.5:9b",
        )
        progress_values.extend(
            float(notification["llm"]["progress"])
            for notification in server.notifications
            if notification.get("llm", {}).get("progress") is not None
        )

        assert 0.5 in progress_values
        assert 0.01 in progress_values

    async def test_model_selection_settings_failure_becomes_retryable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        server = FakeServer([{"choice": "install", "model": "qwen3.5:9b"}])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def select(_model: str) -> None:
            raise settings_module.SettingsUnreadable("settings.json")

        coordinator.set_llm_model_selected_handler(select)
        await coordinator.initialize()

        await coordinator.report_llm(
            LlmSetupState.MODEL_MISSING,
            reason="model_missing",
            model="qwen3.5:9b",
        )

        assert coordinator.state.llm.state is LlmSetupState.MODEL_FAILED
        assert coordinator.state.llm.reason == "settings_save_failed"
        assert len(server.asked) == 2

    async def test_local_model_selection_settings_failure_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        coordinator = SetupCoordinator(FakeServer([]).as_server(), {})
        await coordinator.initialize()
        local = OllamaLocalModel("llama3.1:8b", "llama3.1:8b", 4_200_000_000)

        async def select(_model: str) -> None:
            raise settings_module.SettingsUnreadable("settings.json")

        coordinator.set_llm_model_selected_handler(select)
        await coordinator.select_local_llm_model(local)

        assert coordinator.state.llm.state is LlmSetupState.MODEL_FAILED
        assert coordinator.state.llm.reason == "settings_save_failed"

    async def test_declining_model_pull_never_calls_ollama_pull(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)

        async def pull(*_args: Any, **_kwargs: Any) -> None:
            pytest.fail("pull must only happen after explicit consent")

        monkeypatch.setattr(llm_model_module, "pull_ollama_model", pull)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})
        await coordinator.initialize()

        await coordinator.report_llm(
            LlmSetupState.MODEL_MISSING,
            reason="model_missing",
            model="qwen3.5:9b",
        )

        assert coordinator.state.llm.state is LlmSetupState.MODEL_MISSING

    async def test_existing_local_model_is_selectable_without_a_pull(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        local = OllamaLocalModel("llama3.1:8b", "llama3.1:8b", 4_200_000_000)
        monkeypatch.setattr(llm_model_module, "list_ollama_models", lambda: _local_models(local))

        async def pull(*_args: Any, **_kwargs: Any) -> None:
            pytest.fail("a local model selection must not download")

        monkeypatch.setattr(llm_model_module, "pull_ollama_model", pull)
        selected: list[str] = []
        server = FakeServer([{"choice": "select", "model": local.name}])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def select(model: str) -> None:
            selected.append(model)

        coordinator.set_llm_model_selected_handler(select)
        await coordinator.initialize()
        await coordinator.report_llm(
            LlmSetupState.MODEL_MISSING,
            reason="model_missing",
            model="qwen3.5:9b",
        )

        prompt = next(
            payload for method, payload in server.invocations if method == "stage.setup.prompt"
        )
        assert {
            "model": local.name,
            "display_name": local.display_name,
            "size_bytes": local.size_bytes,
            "installed": True,
        } in prompt["alternatives"]
        assert selected == [local.name]
        assert coordinator.state.llm.model == local.name
        assert coordinator.state.llm.reason == "model_checking"

    async def test_a_missing_ollama_blocks_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """★ **Reversed by ADR-034.** Lumi still neither fetches nor starts Ollama, but a
        reply that never comes looks exactly as broken as never being heard.

        The screen is not a loading screen: it says Ollama is missing and where to get it.
        """
        one_engine(monkeypatch)
        no_ollama(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.set_tts_runtime(EngineRuntime.READY)

        assert boots_of(server)[-1] == "blocked"

    async def test_a_working_stack_is_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All three usable → the character comes out. **The only path to `ready`.**"""
        one_engine(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.set_tts_runtime(EngineRuntime.READY)
        await coordinator.set_stt_runtime(EngineRuntime.READY)

        assert coordinator.boot is BootPhase.READY
        assert boots_of(server)[-1] == "ready"
