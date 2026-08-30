"""First-run setup's flow. **Uses neither WS nor HTTP** (the server is substituted).

Tests 2 / 11 / 22 / 24 / 26 from docs/architecture/setup.md §8, plus a regression test for
a race condition found in practice.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from lumi import paths as paths_module
from lumi import settings as settings_module
from lumi.artifacts.install import SetupError
from lumi.providers.base import EngineRuntime
from lumi.setup import acquire as acquire_module
from lumi.setup import coordinator as coordinator_module
from lumi.setup import detection as detection_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.detect import DetectedEngine
from lumi.setup.ollama import OllamaLocalModel
from lumi.setup.state import (
    BootPhase,
    EmbeddingSetupState,
    LlmSetup,
    LlmSetupState,
    SttSetupState,
    TtsSetupState,
)
from lumi.transport.methods import (
    CHOICE_INDIVIDUALLY,
    COMPONENT_ALL,
    METHOD_SETUP_RECHECK_OLLAMA,
)
from lumi.transport.protocol import Result, Role
from lumi.transport.server import WsServer


class FakeServer:
    """Holds only the two methods of `WsServer` that the Coordinator uses."""

    def __init__(
        self,
        answers: list[str | dict[str, Any] | None],
        *,
        bulk: str | dict[str, Any] | None = CHOICE_INDIVIDUALLY,
    ) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.invocations: list[tuple[str, dict[str, Any]]] = []
        self._answers = list(answers)
        #: **The bulk question comes first now** (roadmap 2g), and it is answered from
        #: here rather than from the script. Most tests are about one component and say
        #: "let me choose individually"; answering it out of `answers` would mean editing
        #: every one of them, and every future one, to account for a question they are not
        #: about. `bulk=None` makes it go unanswered, for the tests that are about it.
        self._bulk = bulk
        self.request_handlers: dict[str, Any] = {}

    @property
    def asked(self) -> list[str]:
        """Which components were asked about, **excluding the bulk question**."""
        return [
            str(payload["component"])
            for method, payload in self.invocations
            if method == "stage.setup.prompt" and payload.get("component") != COMPONENT_ALL
        ]

    def on_request(self, method: str, handler: Any) -> None:
        self.request_handlers[method] = handler

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
        if (payload or {}).get("component") == COMPONENT_ALL:
            if self._bulk is None:
                raise TimeoutError
            answer = self._bulk if isinstance(self._bulk, dict) else {"choice": self._bulk}
            return Result(corr_id="x", ok=True, payload=answer)
        if not self._answers:
            raise TimeoutError
        choice = self._answers.pop(0)
        payload = choice if isinstance(choice, dict) else {"choice": choice}
        return Result(corr_id="x", ok=True, payload=payload)

    def as_server(self) -> WsServer:
        return cast(WsServer, self)


async def _local_models(model: OllamaLocalModel) -> tuple[OllamaLocalModel, ...]:
    return (model,)


@pytest.fixture(autouse=True)
def no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Never asks the developer's own Ollama what it has installed.**

    `list_ollama_models` talks to a fixed local endpoint, so without this the prompt is
    built from whatever models happen to be on the machine running the suite: on a
    machine that already has the recommended model, it comes back as `installed: True`
    with that machine's byte count, and the test asserting the catalog entry fails.

    Same reason as `isolated_paths` below. A test that passes or fails depending on whose
    laptop it runs on is not testing the code.
    """

    async def none() -> tuple[OllamaLocalModel, ...]:
        return ()

    monkeypatch.setattr(coordinator_module, "list_ollama_models", none)


@pytest.fixture(autouse=True)
def isolated_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # **One redirect covers every user-data path**, since they all derive from `data_dir`.
    monkeypatch.setattr(paths_module, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(paths_module, "engines_dir", lambda: tmp_path / "engines")
    # **Never reads the developer's real model directory.** Doing so makes the outcome
    # depend on whose machine the suite runs on
    monkeypatch.setattr(paths_module, "models_dir", lambda: tmp_path / "models")
    # Which STT model setup fetches now comes from the settings file, so it has to be
    # isolated for the same reason
    monkeypatch.setattr(paths_module, "settings_file", lambda: tmp_path / "settings.json")


@pytest.fixture(autouse=True)
def ollama_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default for this file is **"Ollama is installed and running."**

    Without this the outcome depends on whether the machine running the suite happens to
    have Ollama up — which it did, and which is exactly how a phase that now depends on
    all three components would pass here and fail on a clean checkout.
    """

    async def detect(_env: Any) -> DetectedEngine | None:
        return DetectedEngine(
            name="ollama",
            display_name="Ollama",
            port=11434,
            executable=Path("C:/ollama.exe"),
            running=True,
        )

    monkeypatch.setattr(detection_module, "detect_ollama", detect)


@pytest.fixture(autouse=True)
def speech_model_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default for this file is **"the speech model is already there."**

    Otherwise every TTS test would also trip the STT question and have to account for
    it. The STT flow has its own tests below, which opt out of this.
    """
    monkeypatch.setattr(detection_module, "is_model_installed", lambda *_: True)


def no_speech_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection_module, "is_model_installed", lambda *_: False)


def missing_models(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Only the named artifacts are absent. **Two models share this check now** — the
    speech model and the embedding model — so a blanket `lambda: False` would silently
    make the tests about one of them also be about the other.
    """

    def installed(artifact: Any, *_args: Any) -> bool:
        return artifact.name not in names

    monkeypatch.setattr(detection_module, "is_model_installed", installed)


def no_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    async def detect(_env: Any) -> list[DetectedEngine]:
        return []

    monkeypatch.setattr(detection_module, "detect_engines", detect)


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

    monkeypatch.setattr(detection_module, "detect_engines", detect)


def no_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    async def detect(_env: Any) -> DetectedEngine | None:
        return None

    monkeypatch.setattr(detection_module, "detect_ollama", detect)


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

        monkeypatch.setattr(coordinator_module, "pull_ollama_model", pull)
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
        coordinator._model_prompting = True

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

        monkeypatch.setattr(coordinator_module, "pull_ollama_model", pull)
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

        monkeypatch.setattr(coordinator_module, "pull_ollama_model", pull)
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
        monkeypatch.setattr(coordinator_module, "list_ollama_models", lambda: _local_models(local))

        async def pull(*_args: Any, **_kwargs: Any) -> None:
            pytest.fail("a local model selection must not download")

        monkeypatch.setattr(coordinator_module, "pull_ollama_model", pull)
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


def snapshots_of(server: FakeServer) -> list[tuple[str, str, str, str, str]]:
    """Every state broadcast, as `(boot, tts, llm, stt, embedding)`. **In order.**"""
    return [
        (
            str(item["boot"]),
            str(item["tts"]["state"]),
            str(item["llm"]["state"]),
            str(item["stt"]["state"]),
            str(item["embedding"]["state"]),
        )
        for item in server.notifications
        if item["method"] == "stage.setup.state"
    ]


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
