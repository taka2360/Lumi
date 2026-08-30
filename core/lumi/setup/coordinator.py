"""The coordinator for first-run setup. **All decisions are concentrated here**
(the Stage only displays).

Design → docs/architecture/setup.md §2 / §2b

Flow:

```
Startup → detection (local only) → state is determined
Stage connects → state is broadcast
  For each component that is not_configured → ask whether to fetch
    "Fetch" → installing (progress broadcast) → installed / failed
      failed → say so, and offer "retry" / "not now" again
    "Not now" → stays not_configured. **Nothing is written down**
```

## Three components, asked about one at a time

TTS (engine) and STT (model) are both fetched **with consent**; the LLM is
**detected only** (ADR-023). Questions are asked in sequence, never at once:
two consent dialogs on a first run is how a program teaches people to click
through without reading.

**The order is TTS first.** Being unable to speak is the more visible failure,
and it is the one whose fetch the boot screen already waits on.

## Nothing here is remembered across starts

Lumi does not run until all three are usable (ADR-034), so **"not now" is not an
answer to file away** — filing it would mean never asking again about the very
thing that is blocking startup, leaving the user with a screen that says setup is
incomplete and no way to finish it. The question comes back next start.

**Re-asking within one start is a different thing, and it is not done.** Lumi
never repeats a question by itself; the only way back to a question is the user
pressing "retry" on a failure.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import TypeVar

from lumi import logging as lumi_logging
from lumi.providers.base import EngineRuntime
from lumi.setup.acquire import Acquisition
from lumi.setup.broadcast import SetupStateBroadcaster
from lumi.setup.consent import ConsentPrompter
from lumi.setup.detection import ComponentDetector
from lumi.setup.llm_model import LlmModelChooser
from lumi.setup.ollama import (
    OllamaLocalModel,
    OllamaModelArtifact,
)
from lumi.setup.prompter import Prompter, WsPrompter
from lumi.setup.state import (
    BootPhase,
    LlmSetup,
    LlmSetupState,
    SetupSnapshot,
)
from lumi.transport.methods import (
    METHOD_SETUP_RECHECK_OLLAMA,
)
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)

#: What a fetch hands back. The engine returns its executable's path; the speech model
#: returns nothing. **`_install` never looks at it** — it only passes it to `on_installed`.
T = TypeVar("T")

#: How long to wait for the user's choice. **Human time**, so it's long.
PROMPT_TIMEOUT_S = 600.0
OLLAMA_START_GRACE_S = 15.0


class SetupCoordinator:
    def __init__(
        self,
        server: WsServer,
        env: Mapping[str, str],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._server = server
        self._env = env
        self._state = SetupStateBroadcaster(server)
        # **The Stage can connect before detection finishes.** Observed connecting
        # first in practice (2026-08-15). Deciding whether to prompt while state is
        # still unknown would skip asking when it should.
        self._initialized = asyncio.Event()
        # Automatic polling can overlap a slow local API probe. Detection and the transition
        # into model warm-up are one serialized operation, never two warm-ups.
        self._ollama_recheck_lock = asyncio.Lock()
        self._detector = ComponentDetector(env, clock=clock)
        self._acquire = Acquisition(self._state, env)
        self._prompter: Prompter = WsPrompter(server, self._state)
        self._llm_model = LlmModelChooser(self._state, self._prompter)
        self._consent = ConsentPrompter(
            self._state,
            self._prompter,
            self._acquire,
            self._llm_model,
            env,
            ready=self._initialized,
        )
        # Registering is the inbound allowlist (ADR-028). This route can only re-check
        # the fixed local Ollama endpoint; no host or URL comes from Stage.
        server.on_request(METHOD_SETUP_RECHECK_OLLAMA, self._recheck_ollama)

    @property
    def state(self) -> SetupSnapshot:
        return self._state.snapshot

    @property
    def boot(self) -> BootPhase:
        return self._state.boot

    async def initialize(self) -> None:
        """Called exactly once at startup. **No external communication.**"""
        try:
            await self._redetect()
        finally:
            # Even on failure, whatever is waiting is never left hanging.
            self._initialized.set()

    # ── Detection ──────────────────────────────────────────────

    async def _redetect(self) -> None:
        """Looks at all three. **Local filesystem and 127.0.0.1 only** (setup.md §6)."""
        await self._state.replace_all(await self._detector.everything())

    def set_ollama_detected_handler(self, handler: Callable[[], None]) -> None:
        """Registers the runtime's model-check trigger.

        Detection owns only the transition to ``detected × starting``. The Provider
        remains the single place that checks model presence and loads it.
        """
        self._llm_model.on_detected(handler)

    def set_llm_model_selected_handler(self, handler: Callable[[str], Awaitable[None]]) -> None:
        """Registers the runtime update needed when setup selects a different model."""
        self._llm_model.on_selected(handler)

    async def _recheck_ollama(self, _payload: dict[str, object]) -> dict[str, object]:
        """Re-checks Ollama on the local machine and starts model confirmation.

        The one-second UI timer calls this method. There is deliberately no manual button.
        The destination stays fixed in ``detect_ollama`` (filesystem + 127.0.0.1:11434).
        """
        async with self._ollama_recheck_lock:
            check = await self._detector.recheck_ollama()
            if not check.found:
                await self._state.replace(
                    llm=LlmSetup(
                        state=LlmSetupState.NOT_CONFIGURED,
                        reason="Ollama not found",
                        model=self._state.snapshot.llm.model,
                    )
                )
                return {"detected": False, "running": False}

            if not check.running:
                starting = check.starting
                await self._state.replace(
                    llm=LlmSetup(
                        state=LlmSetupState.DETECTED,
                        runtime=(EngineRuntime.STARTING if starting else EngineRuntime.STOPPED),
                        model=self._state.snapshot.llm.model,
                        reason="ollama_starting" if starting else "ollama_not_running",
                    )
                )
                return {"detected": True, "running": False, "starting": starting}

            if self._state.snapshot.llm.state in (
                LlmSetupState.MODEL_INSTALLING,
                LlmSetupState.MODEL_FAILED,
            ) or (
                self._state.snapshot.llm.state is LlmSetupState.MODEL_MISSING
                and self._llm_model.prompting
            ):
                return {"detected": True, "running": True}

            # An untrusted Stage may still send this request after the panel is gone.
            # Re-checking must never downgrade a working conversation to `starting`.
            if self._state.snapshot.llm.ready:
                return {"detected": True, "running": True}
            await self._state.replace(
                llm=LlmSetup(
                    state=LlmSetupState.DETECTED,
                    runtime=EngineRuntime.STARTING,
                    model=self._state.snapshot.llm.model,
                    reason="model_checking",
                )
            )
            self._llm_model.detected()
            return {"detected": True, "running": True}

    # ── Broadcasting ──────────────────────────────────────────────

    async def set_tts_runtime(self, runtime: EngineRuntime) -> None:
        """The TTS engine **process**'s state changed.

        A separate axis from installation state, but it's the same single state
        distributed to the Stage, so **broadcasting is consolidated to this one
        exit point** (sending from two places couldn't guarantee ordering).

        Named for its component, like `set_stt_runtime`. It was `set_runtime` while TTS
        was the only thing with a process; once STT gained a runtime axis (ADR-035) the
        bare name stopped saying which of the two it moved.
        """
        await self._state.replace(tts=replace(self._state.snapshot.tts, runtime=runtime))

    async def report_llm(self, state: LlmSetupState, *, reason: str | None, model: str) -> None:
        """What actually happened when the Provider tried to load.

        **Detection can't see this.** `model_missing` needs Ollama's API, which only the
        Provider talks to — so **whoever found out is the one who reports it**, the same
        rule `warm_tts` follows for the engine process.
        """
        runtime = (
            EngineRuntime.READY
            if state in (LlmSetupState.DETECTED, LlmSetupState.MODEL_MISSING)
            else EngineRuntime.STOPPED
        )
        await self._state.replace(
            llm=LlmSetup(state=state, model=model, reason=reason, runtime=runtime)
        )
        if state is LlmSetupState.MODEL_MISSING:
            await self._llm_model.ask()

    async def select_local_llm_model(self, model: OllamaLocalModel) -> None:
        await self._llm_model.select_local(model)

    async def install_llm_model(self, artifact: OllamaModelArtifact) -> None:
        await self._llm_model.pull(artifact)

    async def set_stt_runtime(self, runtime: EngineRuntime) -> None:
        """The STT Provider's load state changed without changing acquisition state.

        `state: failed` means fetching or verification failed. CTranslate2 failing to load
        an installed model is instead `state: installed × runtime: failed` (ADR-035).
        """
        await self._state.replace(stt=replace(self._state.snapshot.stt, runtime=runtime))

    # ── Fetching ──────────────────────────────────────────────

    async def on_stage_connected(self) -> None:
        """The Stage connected. **Broadcasts the current state and asks if needed.**"""
        await self._consent.run()

    async def install_tts_engine(self) -> None:
        await self._acquire.tts_engine()

    async def install_embedding_model(self) -> None:
        await self._acquire.embedding_model()

    async def install_speech_model(self) -> None:
        await self._acquire.speech_model()
