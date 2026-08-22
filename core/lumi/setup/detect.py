"""Detects existing installations. **No external communication.**

Only looks at the local filesystem and 127.0.0.1
(docs/architecture/setup.md §6).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from lumi import logging as lumi_logging
from lumi import paths
from lumi.setup.engines import AIVISSPEECH_ENGINE, EngineArtifact

log = lumi_logging.get_logger(__name__)

#: Wait time for checking whether a port is open. **Never waits long** (never delays startup).
PROBE_TIMEOUT_S = 0.3
OLLAMA_API_TIMEOUT_S = 1.0


@dataclass(frozen=True, slots=True)
class KnownEngine:
    """The engine to detect."""

    name: str
    display_name: str
    port: int
    # : Held as an environment variable name plus a trailing path, not a path relative to
    # `%LOCALAPPDATA%` etc.
    candidates: tuple[tuple[str, str], ...]


#: Detection targets. VOICEVOX is **not bundled, but one the user installed is used** (ADR-019).
KNOWN_ENGINES: tuple[KnownEngine, ...] = (
    KnownEngine(
        name="aivisspeech",
        display_name="AivisSpeech",
        port=AIVISSPEECH_ENGINE.default_port,
        candidates=(
            ("LOCALAPPDATA", r"Programs\AivisSpeech\AivisSpeech-Engine\run.exe"),
            ("ProgramFiles", r"AivisSpeech\AivisSpeech-Engine\run.exe"),
        ),
    ),
    KnownEngine(
        name="voicevox",
        display_name="VOICEVOX",
        port=50021,
        candidates=(
            ("LOCALAPPDATA", r"Programs\VOICEVOX\vv-engine\run.exe"),
            ("ProgramFiles", r"VOICEVOX\vv-engine\run.exe"),
        ),
    ),
)


#: Ollama. **Lumi neither fetches nor installs it. Detection only** (ADR-023).
#: Unlike the TTS engines, it's not put in `KNOWN_ENGINES` — since it isn't
#: something to be fetched, mixing it into the same list would open a path where it
#: gets treated as fetchable.
OLLAMA: KnownEngine = KnownEngine(
    name="ollama",
    display_name="Ollama",
    port=11434,
    candidates=(
        ("LOCALAPPDATA", r"Programs\Ollama\ollama.exe"),
        ("ProgramFiles", r"Ollama\ollama.exe"),
    ),
)


@dataclass(frozen=True, slots=True)
class DetectedEngine:
    name: str
    display_name: str
    port: int
    executable: Path | None
    #: Whether it's already running. If so, **never started a second time**.
    running: bool
    #: Whether Lumi installed it. **Distinguished from something the user installed themselves**
    #: (this decides whether the state is `installed` or `detected` → setup.md §2).
    managed_by_lumi: bool = False
    #: The version, if Lumi installed it.
    version: str | None = None


def candidate_executables(engine: KnownEngine, env: Mapping[str, str]) -> list[Path]:
    """Assembles candidate paths. **A pure function** (environment variables are passed as an
    argument).
    """
    results: list[Path] = []
    for variable, suffix in engine.candidates:
        base = env.get(variable)
        if base:
            results.append(Path(base) / suffix)
    return results


def find_installed_by_lumi(artifact: EngineArtifact, root: Path) -> Path | None:
    """Finds the executable of an engine Lumi installed."""
    install_dir = root / f"{artifact.name}-{artifact.version}"
    if not install_dir.is_dir():
        return None
    return next(install_dir.rglob(artifact.executable_name), None)


async def is_port_open(port: int, *, host: str = "127.0.0.1") -> bool:
    """**127.0.0.1 only.** Never opens outward."""
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            _reader, writer = await asyncio.open_connection(host, port)
    except (OSError, TimeoutError):
        return False

    writer.close()
    # **Never waits unboundedly on `wait_closed()`.** Depending on the peer's
    # implementation it may never return, and there's no need to wait just to learn
    # "is the port open" (never delays startup).
    with contextlib.suppress(OSError, TimeoutError, asyncio.CancelledError):
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            await writer.wait_closed()
    return True


async def ollama_api_version(
    port: int = 11434, *, transport: httpx.AsyncBaseTransport | None = None
) -> str | None:
    """Returns Ollama's version only when its fixed local API actually answers.

    A TCP listener on port 11434 is not enough to claim "Ollama detected". This probes
    the endpoint shown in the setup flow, with proxy/environment handling disabled so
    the request cannot be redirected outside the machine.
    """
    try:
        async with httpx.AsyncClient(
            timeout=OLLAMA_API_TIMEOUT_S,
            transport=transport,
            trust_env=False,
        ) as client:
            response = await client.get(f"http://127.0.0.1:{port}/api/version")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict) or "version" not in payload:
        return None
    return str(payload["version"])


async def detect_engines(env: Mapping[str, str]) -> list[DetectedEngine]:
    """Enumerates usable engines. **No external communication.**"""
    found: list[DetectedEngine] = []

    lumi_executable = find_installed_by_lumi(AIVISSPEECH_ENGINE, paths.engines_dir())

    for engine in KNOWN_ENGINES:
        executable = next(
            (path for path in candidate_executables(engine, env) if path.is_file()), None
        )
        managed_by_lumi = False
        if executable is None and engine.name == AIVISSPEECH_ENGINE.name:
            executable = lumi_executable
            managed_by_lumi = executable is not None
        running = await is_port_open(engine.port)
        if executable is None and not running:
            continue
        found.append(
            DetectedEngine(
                name=engine.name,
                display_name=engine.display_name,
                port=engine.port,
                executable=executable,
                running=running,
                managed_by_lumi=managed_by_lumi,
                version=AIVISSPEECH_ENGINE.version if managed_by_lumi else None,
            )
        )
        log.info(
            "setup.engine.detected",
            engine=engine.name,
            executable=str(executable) if executable else None,
            running=running,
        )

    return found


def find_on_path(command: str, env: Mapping[str, str]) -> Path | None:
    """Finds the executable via `PATH`. **Environment variables are passed as an argument**
    (testable as a pure function).

    Things assumed to be installed by the user themselves (Ollama) are often found
    on `PATH` rather than at a fixed location. Looking only at hardcoded paths would
    incorrectly report "installed but not found."
    """
    found = shutil.which(command, path=env.get("PATH"))
    return Path(found) if found else None


async def detect_ollama(env: Mapping[str, str]) -> DetectedEngine | None:
    """Looks for Ollama. **No external communication** (local paths and 127.0.0.1 only).

    Returns `None` if not found. This becomes setup.md §2b's `not_configured`.
    **"Not installed" and "not running" are distinguished right here**
    (the Provider only sees HTTP, so it can't tell them apart).
    """
    executable = next(
        (path for path in candidate_executables(OLLAMA, env) if path.is_file()), None
    ) or find_on_path("ollama", env)
    running = await ollama_api_version(OLLAMA.port) is not None
    if executable is None and not running:
        return None
    return DetectedEngine(
        name=OLLAMA.name,
        display_name=OLLAMA.display_name,
        port=OLLAMA.port,
        executable=executable,
        running=running,
    )
