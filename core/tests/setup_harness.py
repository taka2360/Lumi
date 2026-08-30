"""Shared harness for the first-run setup tests. **Neither WS nor HTTP is involved.**

The Coordinator is a facade over five objects now (ADR-045), and the tests follow them
into their own files. What every one of them needs is the same: a substitute server, and
a machine whose answers do not depend on whose laptop the suite is running on.

Imported the way `fakes.py` is — **the fixtures here are autouse**, so importing the name
is what switches them on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from lumi import paths as paths_module
from lumi.setup import detection as detection_module
from lumi.setup import llm_model as llm_model_module
from lumi.setup.detect import DetectedEngine
from lumi.setup.ollama import OllamaLocalModel
from lumi.transport.methods import (
    CHOICE_INDIVIDUALLY,
    COMPONENT_ALL,
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

    monkeypatch.setattr(llm_model_module, "list_ollama_models", none)


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
