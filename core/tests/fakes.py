"""Test doubles and fixtures shared by the startup tests.

**Not `conftest.py` on purpose.** `conftest.py` is for fixtures and hooks that pytest
collects automatically; the autouse fixtures here must apply to the startup tests and
**not** to the whole suite (`isolated_paths` repoints `lumi.paths`, and tests that read
the real Content Pack would change meaning under it). A plain module keeps the blast
radius to the files that import it:

```python
from fakes import isolated_paths, no_ollama  # noqa: F401  — autouse fixtures
```

pytest registers a fixture that is imported into a test module's namespace, so importing
it is what opts a file in.

`core/tests/` has no `__init__.py`, so pytest's prepend import mode puts this directory
on `sys.path` and the import above resolves. mypy checks it via `files = ["lumi", "tests"]`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from lumi import paths as paths_module
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderKind,
    ProviderUnavailable,
    ResourceHint,
    UnloadPolicy,
)
from lumi.setup import coordinator as coordinator_module
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.detect import DetectedEngine
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer


class FakeServer:
    """Holds only what the Coordinator and the runtime touch."""

    def __init__(self) -> None:
        self.boots: list[str] = []
        self.stt_runtimes: list[str] = []
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
            self.stt_runtimes.append(str(payload["stt"]["runtime"]))
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
    # ★ **Nothing under here touches the real `%LOCALAPPDATA%\Lumi`.** Every user-data
    # path is derived from `data_dir`, so redirecting that one covers the databases, the
    # database key and anything added later — a test that writes into the developer's own
    # memory database is not a test, it is a conversation nobody had.
    monkeypatch.setattr(paths_module, "data_dir", lambda: tmp_path / "data")
    # **Never reads the developer's real model directory or settings.** Whether the boot
    # phase reaches `ready` now depends on all three components (ADR-034), so anything
    # left pointing at the real machine makes the outcome depend on whose machine it is.
    monkeypatch.setattr(paths_module, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(paths_module, "settings_file", lambda: tmp_path / "settings.json")


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detection must not touch 127.0.0.1 during tests.

    **The default is "not installed"** — the state that blocks startup — so a test that
    wants Lumi to actually come out has to say so (`conversation_is_possible`).
    """

    async def detect(_env: Any) -> DetectedEngine | None:
        return None

    monkeypatch.setattr(coordinator_module, "detect_ollama", detect)


def conversation_is_possible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything except the TTS engine is in place — **including at detection time.**

    The speech model is a filesystem check, and Ollama is settled either by detection or by
    `warm_llm` reporting. Both have to say yes here: **the boot phase now answers what is
    already settled against it before it shows any wait** (ADR-034 / setup.md §2b), so a
    missing LLM would keep the phase at `blocked` and no `starting` would ever be broadcast.
    """
    monkeypatch.setattr(coordinator_module, "is_model_installed", lambda *_: True)

    async def detect(_env: Any) -> DetectedEngine | None:
        return DetectedEngine(
            name="ollama",
            display_name="Ollama",
            port=11434,
            executable=Path("C:/ollama.exe"),
            running=True,
        )

    monkeypatch.setattr(coordinator_module, "detect_ollama", detect)


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
