"""Looking at the machine to see what is already here. **Reads, never fetches.**

Design → docs/architecture/setup.md §2 / §2b / §6

Nothing in this module reaches a network host. The filesystem answers whether a model
is unpacked, and `detect_ollama` answers whether a local runtime is up on 127.0.0.1 —
**"no external communication until the user chooses to"** (ADR-019) is a property of
this file being unable to do otherwise, not of anyone remembering.

## Installed and running are different questions

Over HTTP they are indistinguishable: an engine that is not installed and one that is
installed but stopped both fail to answer. Telling someone to install what they already
have is how they start doubting their own machine, so the two are found out different
ways — the executable from the filesystem, the liveness from the endpoint — and reported
as separate axes (docs/architecture/setup.md §2b).

## The grace period

An Ollama that is installed but not answering has usually just been started. Reporting
"not running" immediately would put a "start Ollama" instruction on screen a second
before it starts answering. The deadline lives here because it belongs to the act of
looking, and both the first detection and every re-check consult the same one.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from lumi import logging as lumi_logging
from lumi import paths, settings
from lumi.artifacts.install import is_model_installed
from lumi.artifacts.models import (
    FASTER_WHISPER_LARGE_V3_TURBO,
    HARRIER_OSS_V1_270M,
    STT_MODELS,
    ModelArtifact,
)
from lumi.providers.base import EngineRuntime
from lumi.setup.detect import detect_engines, detect_ollama
from lumi.setup.state import (
    EmbeddingSetup,
    EmbeddingSetupState,
    LlmSetup,
    LlmSetupState,
    SetupSnapshot,
    SttSetup,
    SttSetupState,
    TtsSetup,
    TtsSetupState,
)

log = lumi_logging.get_logger(__name__)

#: How long an installed-but-silent Ollama is called "starting" rather than "not running".
OLLAMA_START_GRACE_S: Final = 15.0

#: What `stt_model` resolves to when nothing selects another one. **Pinned**
#: (docs/architecture/setup.md §3b)
DEFAULT_STT_ARTIFACT: ModelArtifact = FASTER_WHISPER_LARGE_V3_TURBO


def selected_stt_artifact(env: Mapping[str, str]) -> ModelArtifact | None:
    """Which STT model to check for and fetch — **the one the Provider will look for.**

    `FasterWhisperProvider` is built from `settings.stt_model`, so a fixed artifact here
    lets setup install one model, report `installed`, and leave the Provider looking for
    another: **Lumi is deaf while the screen says it is ready.** That also made
    `LUMI_STT_MODEL=small`, which [ADR-027] keeps as the way back to a lighter model,
    silently not work — for exactly the users who need it.

    `None` when the selected name is not pinned. **Never substitutes a different model**:
    fetching something else is the mismatch this exists to prevent.
    """
    name = settings.load(paths.settings_file(), env).stt_model.value
    artifact = STT_MODELS.get(name)
    if artifact is None:
        log.warning("setup.stt.unpinned_model", model=name, pinned=sorted(STT_MODELS))
    return artifact


@dataclass(frozen=True, slots=True)
class OllamaCheck:
    """What one look at the local Ollama found. **A finding, not a decision.**

    Re-checking has to answer three different states and the caller turns each into
    different setup state, sometimes into none at all — an already-working conversation
    must never be downgraded by a re-check. Returning the finding keeps that judgement
    where the state lives, and keeps this module to looking.
    """

    found: bool
    running: bool
    #: Only meaningful when found and not running: **within the start grace period.**
    starting: bool = False


class ComponentDetector:
    """Answers what is on this machine, for all four components."""

    __slots__ = ("_clock", "_env", "_ollama_start_deadline")

    def __init__(
        self, env: Mapping[str, str], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._env = env
        self._clock = clock
        self._ollama_start_deadline: float | None = None

    async def everything(self) -> SetupSnapshot:
        """Looks at all four. **Local filesystem and 127.0.0.1 only** (setup.md §6)."""
        return SetupSnapshot(
            tts=await self._detect_tts(),
            llm=await self._detect_llm(),
            stt=await asyncio.to_thread(self._detect_stt),
            embedding=await asyncio.to_thread(self._detect_embedding),
        )

    async def recheck_ollama(self) -> OllamaCheck:
        """Looks again, and keeps the grace period. **Decides nothing about setup state.**"""
        found = await detect_ollama(self._env)
        if found is None:
            self._ollama_start_deadline = None
            return OllamaCheck(found=False, running=False)
        if not found.running:
            now = self._clock()
            if self._ollama_start_deadline is None:
                self._ollama_start_deadline = now + OLLAMA_START_GRACE_S
            return OllamaCheck(
                found=True, running=False, starting=now < self._ollama_start_deadline
            )
        self._ollama_start_deadline = None
        return OllamaCheck(found=True, running=True)

    async def _detect_tts(self) -> TtsSetup:
        engines = await detect_engines(self._env)
        usable = next((engine for engine in engines if engine.executable or engine.running), None)
        if usable is None:
            return TtsSetup(state=TtsSetupState.NOT_CONFIGURED)
        # Distinguishes what Lumi installed from what the user installed themselves (setup.md §2).
        return TtsSetup(
            state=(TtsSetupState.INSTALLED if usable.managed_by_lumi else TtsSetupState.DETECTED),
            engine_name=usable.display_name,
            version=usable.version,
            port=usable.port,
            executable=str(usable.executable) if usable.executable else None,
        )

    async def _detect_llm(self) -> LlmSetup:
        """**Only "is Ollama there".** Whether the *model* is there needs an API call, which
        is the Provider's job — `report_llm` narrows this to `model_missing` later.

        Getting these from different places is deliberate: "not installed" and "installed
        but not running" are indistinguishable over HTTP, and telling someone to install
        what they already have is how they start doubting their own machine
        (docs/architecture/setup.md §2b).
        """
        found = await detect_ollama(self._env)
        if found is None:
            self._ollama_start_deadline = None
            return LlmSetup(
                state=LlmSetupState.NOT_CONFIGURED, reason="Ollama not found", model=None
            )
        if not found.running:
            self._ollama_start_deadline = self._clock() + OLLAMA_START_GRACE_S
            return LlmSetup(
                state=LlmSetupState.DETECTED,
                runtime=EngineRuntime.STARTING,
                reason="ollama_starting",
            )
        self._ollama_start_deadline = None
        return LlmSetup(
            state=LlmSetupState.DETECTED,
            runtime=EngineRuntime.READY,
        )

    def _detect_stt(self) -> SttSetup:
        """A file check, not a process check. **Never touches the network** — a half-fetched
        directory reads as not-installed (`is_model_installed` checks every pinned size).
        """
        artifact = selected_stt_artifact(self._env)
        if artifact is None:
            # There is nothing to fetch, and the Provider will refuse the same name.
            # **Said out loud** rather than installing something else and calling it done
            return SttSetup(state=SttSetupState.NOT_CONFIGURED, model=None, reason="unpinned_model")
        installed = is_model_installed(artifact, paths.stt_models_dir())
        return SttSetup(
            state=SttSetupState.INSTALLED if installed else SttSetupState.NOT_CONFIGURED,
            model=artifact.name,
        )

    def _detect_embedding(self) -> EmbeddingSetup:
        """Whether the embedding model is on disk (ADR-041). **A file check, like STT's.**"""
        installed = is_model_installed(HARRIER_OSS_V1_270M, paths.embedding_models_dir())
        return EmbeddingSetup(
            state=(
                EmbeddingSetupState.INSTALLED if installed else EmbeddingSetupState.NOT_CONFIGURED
            ),
            model=HARRIER_OSS_V1_270M.name,
        )
