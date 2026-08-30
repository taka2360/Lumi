"""Fetching the speech and embedding models: progress, failure, and retry."""

from __future__ import annotations

import asyncio
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

from lumi.artifacts.install import SetupError
from lumi.providers.base import EngineRuntime
from lumi.setup import acquire as acquire_module
from lumi.setup import detection as detection_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.state import (
    EmbeddingSetupState,
    SttSetupState,
)


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

        # **The embedding model is asked about last**, and only after the three that
        # gate startup — declining it leaves Lumi fully able to talk (ADR-041).
        assert server.asked == ["tts", "stt", "embedding"]

    async def test_does_not_ask_when_the_model_is_already_there(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_engine(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.asked == []

    async def test_an_unanswered_stt_prompt_does_not_repeat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When TTS is available, an unanswered STT question is still sent only once."""
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.asked == ["stt"]

    async def test_fetches_when_the_user_asks_for_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)
        fetched: list[str] = []

        async def fake_install(artifact: Any, models_dir: Path, *, progress: Any = None) -> Path:
            del models_dir
            fetched.append(artifact.name)
            await progress(1.0)
            return Path("C:/models/small")

        monkeypatch.setattr(acquire_module, "install_model", fake_install)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        # **Not hardcoded.** Which model ships is a decision that moves (ADR-027); that this
        # path fetches *the one the rest of Core will look for* is what must not move
        assert fetched == [detection_module.DEFAULT_STT_ARTIFACT.name]
        assert coordinator.state.stt.state is SttSetupState.INSTALLED
        assert states_of(server, "stt")[-1] == "installed"

    async def test_an_override_changes_which_model_is_fetched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ Regression: **`LUMI_STT_MODEL=small` fetched `large-v3-turbo` and reported it
        installed**, while the Provider went looking for `small`.

        Lumi was then deaf with `installed` on screen — and the lighter model is exactly
        what someone reaches for when the big one does not fit their machine (ADR-027).
        """
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)
        fetched: list[str] = []

        async def fake_install(artifact: Any, models_dir: Path, *, progress: Any = None) -> Path:
            del models_dir
            fetched.append(artifact.name)
            await progress(1.0)
            return Path("C:/models/small")

        monkeypatch.setattr(acquire_module, "install_model", fake_install)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {"LUMI_STT_MODEL": "small"})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert fetched == ["small"]

    async def test_an_unpinned_model_is_never_replaced_by_a_different_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Fetching something else would be the same mismatch, one step later.**

        Nothing can be fetched for a name that is not pinned, so nothing is asked either —
        a question whose only answer fails is worse than no question.
        """
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {"LUMI_STT_MODEL": "tiny"})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.stt.state is SttSetupState.NOT_CONFIGURED
        assert coordinator.state.stt.reason == "unpinned_model"
        assert "stt" not in server.asked, "the coordinator asked to fetch an unpinned model"

    async def test_declining_leaves_it_not_configured_and_blocks_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ **"Speaks but cannot listen" stopped being a state Lumi runs in** (ADR-034).

        A character standing there that cannot hear you is one you talk to and get nothing
        from — which reads as broken, not as unfinished.
        """
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()
        # The engine is detected but nothing has started it yet, so the phase is still
        # `starting`. **What settles it is the engine reporting in** — after which the
        # missing speech model is the one thing left, and it is enough on its own.
        await coordinator.set_tts_runtime(EngineRuntime.READY)

        assert coordinator.state.stt.state is SttSetupState.NOT_CONFIGURED
        assert boots_of(server)[-1] == "blocked"

    async def test_a_failed_fetch_never_reverts_to_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Tried and failed is not the same as never tried.** The reason is kept."""
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)

        async def fake_install(*_args: Any, **_kwargs: Any) -> Path:
            raise SetupError("hash_mismatch", "Corrupted")

        monkeypatch.setattr(acquire_module, "install_model", fake_install)
        server = FakeServer(["install", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.stt.state is SttSetupState.FAILED
        assert coordinator.state.stt.reason == "hash_mismatch"

    @pytest.mark.parametrize(
        ("exception_type", "reason"),
        [(RuntimeError, "unexpected_error"), (asyncio.CancelledError, "cancelled")],
        ids=["unexpected-error", "cancelled"],
    )
    async def test_stt_install_records_unexpected_failure_and_reraises_cancellation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exception_type: type[BaseException],
        reason: str,
    ) -> None:
        async def failing_install(*_args: Any, **_kwargs: Any) -> Path:
            raise exception_type("download failed")

        monkeypatch.setattr(acquire_module, "install_model", failing_install)
        coordinator = SetupCoordinator(FakeServer([]).as_server(), {})

        if exception_type is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await coordinator.install_speech_model()
        else:
            await coordinator.install_speech_model()

        assert coordinator.state.stt.state is SttSetupState.FAILED
        assert coordinator.state.stt.reason == reason

    async def test_fetching_the_model_never_disturbs_the_engine_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**The three components are independent.** One update must not blank the others."""
        one_engine(monkeypatch)
        no_speech_model(monkeypatch)

        async def fake_install(artifact: Any, models_dir: Path, *, progress: Any = None) -> Path:
            del artifact, models_dir, progress
            return Path("C:/models/small")

        monkeypatch.setattr(acquire_module, "install_model", fake_install)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        before = coordinator.state.tts
        await coordinator.on_stage_connected()

        assert coordinator.state.tts == before


class TestEmbeddingModel:
    """★ **The one optional download** (ADR-041)."""

    async def test_it_is_fetched_when_the_user_asks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        one_engine(monkeypatch)
        missing_models(monkeypatch, "harrier-oss-v1-270m")
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def fake_model(artifact: Any, *_args: Any, **_kwargs: Any) -> Path:
            return tmp_path / str(artifact.name)

        monkeypatch.setattr(acquire_module, "install_model", fake_model)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.asked == ["embedding"]
        assert coordinator.state.embedding.state is EmbeddingSetupState.INSTALLED
        assert coordinator.state.embedding.model == "harrier-oss-v1-270m"

    async def test_declining_it_still_leaves_lumi_usable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ **This is why it is asked last.** Every other "not now" ends in `blocked`;
        this one does not, because similarity search is a feature and talking is the
        product (ADR-041).
        """
        one_engine(monkeypatch)
        missing_models(monkeypatch, "harrier-oss-v1-270m")
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.embedding.state is EmbeddingSetupState.NOT_CONFIGURED
        # **Declining is not a failure**, and the boot phase says so: `ready` is still
        # reachable, which is the difference from every other component.
        assert boots_of(server)[-1] != "blocked"

    async def test_a_failed_fetch_says_why_and_offers_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**A failure is never smoothed into "done"** — the same rule as every other fetch."""
        one_engine(monkeypatch)
        missing_models(monkeypatch, "harrier-oss-v1-270m")
        server = FakeServer(["install", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def failing(*_args: Any, **_kwargs: Any) -> Path:
            raise SetupError("network_unreachable", "no route")

        monkeypatch.setattr(acquire_module, "install_model", failing)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.embedding.state is EmbeddingSetupState.FAILED
        assert coordinator.state.embedding.reason == "network_unreachable"
        assert server.asked.count("embedding") == 2
