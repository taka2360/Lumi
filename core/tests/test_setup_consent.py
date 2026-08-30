"""What is asked, in what order, and what each answer means."""

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
from lumi.setup import acquire as acquire_module
from lumi.setup import detection as detection_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.detect import DetectedEngine
from lumi.setup.state import (
    EmbeddingSetupState,
    SttSetupState,
    TtsSetupState,
)
from lumi.transport.methods import (
    COMPONENT_ALL,
)


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

        monkeypatch.setattr(detection_module, "detect_engines", slow_detect)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        initializing = asyncio.create_task(coordinator.initialize())
        await started.wait()
        # The Stage connects before detection finishes
        connected = asyncio.create_task(coordinator.on_stage_connected())
        await asyncio.gather(initializing, connected)

        assert [method for method, _ in server.invocations] == [
            "stage.setup.prompt",
            "stage.setup.prompt",
        ]

    async def test_declining_leaves_setup_incomplete(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ **"Not now" no longer starts Lumi** (ADR-034), and nothing is written down.

        The state is still reported explicitly as "not configured" — what changed is that
        the phase ends at `blocked` instead of `ready`, so the screen says setup is
        unfinished rather than putting a character out that can never speak.
        """
        no_engines(monkeypatch)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.tts.state is TtsSetupState.NOT_CONFIGURED
        assert boots_of(server)[-1] == "blocked"
        written = await asyncio.to_thread(lambda: list(tmp_path.glob("*.json")))
        assert written == [], "the answer was persisted"

    async def test_asks_again_on_the_next_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """★ **Reversed by ADR-034.** The answer used to be remembered forever.

        Setup is now a precondition for Lumi running, so remembering "not now" would mean
        never asking again about the one thing blocking startup — **a screen saying setup
        is incomplete, with no way to finish it.**
        """
        no_engines(monkeypatch)
        server = FakeServer(["skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        again = SetupCoordinator(server.as_server(), {})
        await again.initialize()
        await again.on_stage_connected()

        assert len(server.asked) == 2, "the prompt was not repeated on the second start"
        assert server.asked == ["tts", "tts"]
        assert again.state.tts.state is TtsSetupState.NOT_CONFIGURED

    async def test_does_not_repeat_the_question_by_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**Lumi never re-asks on its own within one start.** The only way back to a
        question is the user pressing retry on a failure (docs/architecture/setup.md §2).
        """
        no_engines(monkeypatch)
        server = FakeServer(["skip", "skip", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert len(server.asked) == 1

    async def test_an_unanswered_prompt_stops_asking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nobody answered, so **nothing is inferred from the silence** and nothing loops."""
        no_engines(monkeypatch)
        server = FakeServer([])  # No answer comes back → TimeoutError
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert len(server.asked) == 1
        assert boots_of(server)[-1] == "blocked", "the coordinator is still waiting for an answer"

    async def test_an_unanswered_tts_prompt_does_not_ask_for_stt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unanswered first question interrupts the sequence before the next component."""
        no_engines(monkeypatch)
        no_speech_model(monkeypatch)
        server = FakeServer([])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.asked == ["tts"]

    async def test_offers_a_retry_after_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        no_engines(monkeypatch)
        server = FakeServer(["install", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def failing_install(*_args: Any, **_kwargs: Any) -> Path:
            raise SetupError("network_unreachable")

        monkeypatch.setattr(acquire_module, "install_engine", failing_install)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert len(server.asked) == 2, "a failure should re-offer the choices"
        assert server.invocations[2][1]["retry"] is True
        assert server.invocations[2][1]["reason"] == "network_unreachable"
        # **A failure never reverts to "not yet attempted."**
        assert coordinator.state.tts.state is TtsSetupState.FAILED
        assert coordinator.state.tts.reason == "network_unreachable"
        assert states_of(server)[-1] == "failed"
        # ★ **A failed fetch is never treated as setup being done** (setup.md §8 test 22).
        assert boots_of(server)[-1] == "blocked"

    @pytest.mark.parametrize(
        ("exception_type", "reason"),
        [(RuntimeError, "unexpected_error"), (asyncio.CancelledError, "cancelled")],
        ids=["unexpected-error", "cancelled"],
    )
    async def test_tts_install_records_unexpected_failure_and_reraises_cancellation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exception_type: type[BaseException],
        reason: str,
    ) -> None:
        async def failing_install(*_args: Any, **_kwargs: Any) -> Path:
            raise exception_type("download failed")

        monkeypatch.setattr(acquire_module, "install_engine", failing_install)
        coordinator = SetupCoordinator(FakeServer([]).as_server(), {})

        if exception_type is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await coordinator.install_tts_engine()
        else:
            await coordinator.install_tts_engine()

        assert coordinator.state.tts.state is TtsSetupState.FAILED
        assert coordinator.state.tts.reason == reason

    async def test_retrying_is_not_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """★ **Every repetition is one the user asked for** (ADR-034).

        A cap meant that a connection coming back one attempt too late could only be used
        by restarting Lumi. Lumi still never re-asks by itself — the loop only turns
        because "retry" was pressed.
        """
        no_engines(monkeypatch)
        attempts = 0

        async def failing_install(*_args: Any, **_kwargs: Any) -> Path:
            nonlocal attempts
            attempts += 1
            raise SetupError("network_unreachable")

        monkeypatch.setattr(acquire_module, "install_engine", failing_install)
        server = FakeServer(["install", "install", "install", "install", "skip"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert attempts == 4, "retry attempts were capped"
        assert all(payload["retry"] for _method, payload in server.invocations[2:])

    async def test_a_successful_retry_leaves_the_failure_behind(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """**The point of the retry button**: the second attempt finishes setup."""
        no_engines(monkeypatch)
        attempts = 0

        async def install(*_args: Any, **_kwargs: Any) -> Path:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise SetupError("network_unreachable")
            return tmp_path / "engines" / "aivisspeech-1.2.0" / "run.exe"

        monkeypatch.setattr(acquire_module, "install_engine", install)
        server = FakeServer(["install", "install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.tts.state is TtsSetupState.INSTALLED
        assert coordinator.state.tts.reason is None

    async def test_installs_when_the_user_asks_for_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        no_engines(monkeypatch)
        server = FakeServer(["install"])
        coordinator = SetupCoordinator(server.as_server(), {})

        async def fake_install(*_args: Any, **_kwargs: Any) -> Path:
            return tmp_path / "engines" / "aivisspeech-1.2.0" / "run.exe"

        monkeypatch.setattr(acquire_module, "install_engine", fake_install)

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert coordinator.state.tts.state is TtsSetupState.INSTALLED
        # Also broadcast when asking starts and when answering ends, so the same state repeats
        # in a row. **Two questions now**: the bulk one and then this component's.
        assert states_of(server) == [
            "not_configured",
            "not_configured",
            "not_configured",
            "not_configured",
            "installing",
            "installed",
            "installed",
        ]
        # **The transition as seen by the Stage.** Progresses one-way: not-set-up → question →
        # fetching → engine starting. **Never returns to `setup` after `installing`**
        # (returning would flash the question screen). Right after fetching it's `starting`.
        # **Marking it `ready` would show the character only to pull it back.**
        assert boots_of(server) == [
            "blocked",
            "blocked",
            "setup",
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

        monkeypatch.setattr(acquire_module, "install_engine", fake_install)

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


class TestFetchingEverythingAtOnce:
    """★ **One question with the total, before four questions with four numbers.**

    Four consecutive prompts, each with its own size, make the number that actually
    matters — what this will cost altogether — the one number nobody is shown.
    """

    async def test_the_question_carries_the_total_and_what_makes_it_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        no_engines(monkeypatch)
        missing_models(monkeypatch, "large-v3-turbo", "harrier-oss-v1-270m")
        server = FakeServer([], bulk="skip")
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        asked = server.invocations[0][1]
        assert asked["component"] == COMPONENT_ALL
        items = asked["items"]
        assert [item["component"] for item in items] == ["tts", "stt", "embedding"]
        # **The total is the sum of what is listed**, not a second number that can drift
        # from it. Someone deciding on "12.4 GB" has to be able to see where it comes from.
        assert asked["total_bytes"] == sum(int(item["size_bytes"]) for item in items)
        assert all(int(item["size_bytes"]) > 0 for item in items)

    async def test_choosing_everything_fetches_without_asking_again(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ **"Yes, all of it" is an answer to all of it.** Asking again per component
        would make the first answer meaningless, and is exactly the repetition it exists
        to remove.
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

        assert server.asked == []
        assert coordinator.state.tts.state is TtsSetupState.INSTALLED
        assert coordinator.state.stt.state is SttSetupState.INSTALLED
        assert coordinator.state.embedding.state is EmbeddingSetupState.INSTALLED

    async def test_declining_the_lot_does_not_ask_component_by_component(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ "Not now" means not now. **Following it with four more questions asks the
        same thing four more times**, which is how a consent prompt becomes something
        people learn to dismiss without reading.
        """
        no_engines(monkeypatch)
        missing_models(monkeypatch, "large-v3-turbo", "harrier-oss-v1-270m")
        server = FakeServer([], bulk="skip")
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.asked == []
        assert coordinator.state.tts.state is TtsSetupState.NOT_CONFIGURED
        assert boots_of(server)[-1] == "blocked"

    async def test_nothing_missing_asks_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**A consent dialog for zero bytes teaches people to dismiss consent dialogs.**"""
        one_engine(monkeypatch)
        missing_models(monkeypatch)
        server = FakeServer([], bulk=None)
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.invocations == []

    async def test_an_unanswered_bulk_question_fetches_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nobody is there to answer. **Stop asking**, and never read silence as a yes."""
        no_engines(monkeypatch)
        missing_models(monkeypatch, "large-v3-turbo", "harrier-oss-v1-270m")
        server = FakeServer([], bulk=None)
        coordinator = SetupCoordinator(server.as_server(), {})

        await coordinator.initialize()
        await coordinator.on_stage_connected()

        assert server.asked == []
        assert coordinator.state.tts.state is TtsSetupState.NOT_CONFIGURED
