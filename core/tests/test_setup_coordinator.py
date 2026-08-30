"""What the facade itself is responsible for: the order the Stage is shown."""

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

from lumi.setup import acquire as acquire_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.transport.methods import (
    CHOICE_INDIVIDUALLY,
    COMPONENT_ALL,
    METHOD_SETUP_RECHECK_OLLAMA,
)


class TestTheSequenceTheStageSees:
    """**Characterization tests.** What the Stage receives, in order, and how many times.

    The rest of this file asks whether each component ends up in the right state. None of
    it says how many broadcasts it took to get there, or what order the phases came in —
    and those are exactly what a refactor of the Coordinator can change without failing a
    single assertion elsewhere.

    The duplicates below are deliberate. **A snapshot that repeats is a snapshot the Stage
    re-renders**, and a split that coalesces or doubles one is a behaviour change even
    though every end state matches.
    """

    async def test_the_coordinator_registers_exactly_one_request_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Constructing it wires the Stage's re-check.** Nothing else here answers a
        request; everything else is broadcast or asked.
        """
        no_engines(monkeypatch)
        server = FakeServer([])
        SetupCoordinator(server.as_server(), {})

        assert sorted(server.request_handlers) == [METHOD_SETUP_RECHECK_OLLAMA]

    async def test_a_declined_first_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**Blocked, asked, blocked again.** Declining is a complete answer, and the phase
        goes back to what it was — `setup` is only true while the question is open
        (ADR-034).
        """
        no_engines(monkeypatch)
        missing_models(monkeypatch, "large-v3-turbo", "harrier-oss-v1-270m")
        server = FakeServer([], bulk="skip")
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert snapshots_of(server) == [
            ("blocked", "not_configured", "detected", "not_configured", "not_configured"),
            ("blocked", "not_configured", "detected", "not_configured", "not_configured"),
            ("setup", "not_configured", "detected", "not_configured", "not_configured"),
            ("blocked", "not_configured", "detected", "not_configured", "not_configured"),
        ]

    async def test_an_accepted_first_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**One component at a time, and the phase drops back between them.**

        `installing` is per component, so the phase leaves it and returns for the next one.
        The embedding model is the one that does not block startup (ADR-041 / rev.22), which
        is why the phase reaches `starting` while it is still being fetched.
        """
        no_engines(monkeypatch)
        missing_models(monkeypatch, "large-v3-turbo", "harrier-oss-v1-270m")
        server = FakeServer([], bulk="install")
        coordinator = SetupCoordinator(server.as_server(), {})

        async def fake_engine(*_args: Any, **_kwargs: Any) -> Path:
            return tmp_path / "engines" / "aivisspeech-1.2.0" / "run.exe"

        async def fake_model(artifact: Any, *_args: Any, **_kwargs: Any) -> Path:
            return tmp_path / str(artifact.name)

        monkeypatch.setattr(acquire_module, "install_engine", fake_engine)
        monkeypatch.setattr(acquire_module, "install_model", fake_model)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert snapshots_of(server) == [
            ("blocked", "not_configured", "detected", "not_configured", "not_configured"),
            ("blocked", "not_configured", "detected", "not_configured", "not_configured"),
            ("setup", "not_configured", "detected", "not_configured", "not_configured"),
            ("installing", "installing", "detected", "not_configured", "not_configured"),
            ("blocked", "installed", "detected", "not_configured", "not_configured"),
            ("installing", "installed", "detected", "installing", "not_configured"),
            ("starting", "installed", "detected", "installed", "not_configured"),
            ("installing", "installed", "detected", "installed", "installing"),
            ("starting", "installed", "detected", "installed", "installed"),
            ("starting", "installed", "detected", "installed", "installed"),
        ]

    async def test_the_prompt_order_is_the_bulk_question_then_each_component(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The total comes before the parts** (roadmap 2g). Which component is asked
        about after that, and in what order, is what the split must not reshuffle.
        """
        no_engines(monkeypatch)
        missing_models(monkeypatch, "large-v3-turbo", "harrier-oss-v1-270m")
        server = FakeServer(["skip", "skip", "skip"], bulk=CHOICE_INDIVIDUALLY)
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert [payload.get("component") for _, payload in server.invocations] == [
            COMPONENT_ALL,
            "tts",
            "stt",
            "embedding",
        ]
