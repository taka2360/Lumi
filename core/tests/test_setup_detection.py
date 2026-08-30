"""What setup finds on the machine before it asks anything."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from setup_harness import (  # noqa: F401  — the fixtures here are autouse
    FakeServer,
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

from lumi.setup import detection as detection_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.detect import DetectedEngine
from lumi.setup.state import (
    TtsSetupState,
)


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

        monkeypatch.setattr(detection_module, "detect_engines", detect)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()

        assert coordinator.state.tts.state is TtsSetupState.INSTALLED
        assert coordinator.state.tts.version == "1.2.0"
