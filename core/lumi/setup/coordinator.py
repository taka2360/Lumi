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
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeVar

from lumi import logging as lumi_logging
from lumi.artifacts.engines import AIVISSPEECH_ENGINE
from lumi.artifacts.models import (
    FASTER_WHISPER_LARGE_V3_TURBO,
    HARRIER_OSS_V1_270M,
    ModelArtifact,
)
from lumi.providers.base import EngineRuntime
from lumi.setup.acquire import Acquisition
from lumi.setup.broadcast import SetupStateBroadcaster
from lumi.setup.detection import ComponentDetector, selected_stt_artifact
from lumi.setup.llm_model import LlmModelChooser
from lumi.setup.ollama import (
    OLLAMA_MODELS,
    QWEN_35_9B,
    OllamaLocalModel,
    OllamaModelArtifact,
)
from lumi.setup.prompter import Prompter, WsPrompter
from lumi.setup.state import (
    BootPhase,
    EmbeddingSetupState,
    LlmSetup,
    LlmSetupState,
    SetupSnapshot,
    SttSetupState,
    TtsSetupState,
)
from lumi.transport.methods import (
    CHOICE_INDIVIDUALLY,
    CHOICE_INSTALL,
    COMPONENT_ALL,
    COMPONENT_EMBEDDING,
    COMPONENT_LLM_MODEL,
    COMPONENT_STT,
    COMPONENT_TTS,
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

#: What `stt_model` resolves to when nothing selects another one. **Pinned**
#: (docs/architecture/setup.md §3b)
DEFAULT_STT_ARTIFACT: ModelArtifact = FASTER_WHISPER_LARGE_V3_TURBO


@dataclass(frozen=True, slots=True)
class MissingComponent:
    """One thing that is not on this machine yet, and what fetching it would cost."""

    component: str
    #: What to call it on screen. **Not translated** — it is a product name (AivisSpeech,
    #: `harrier-oss-v1-270m`), and translating a name makes it unsearchable.
    name: str
    size_bytes: int

    def to_payload(self) -> dict[str, object]:
        return {"component": self.component, "name": self.name, "size_bytes": self.size_bytes}


class BulkAnswer(StrEnum):
    """What the user said to "fetch everything missing?".

    **`DECLINED` and `UNANSWERED` are different.** One is a decision to keep for this
    session; the other is nobody being there, which means asking again next start.
    """

    ALL = "all"
    INDIVIDUALLY = "individually"
    DECLINED = "declined"
    UNANSWERED = "unanswered"


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
        # **Kept as two separate flags.** `_prompting` is "is this sequence
        # currently in progress" (prevents duplicate runs); the broadcaster's
        # `asking` is "is a question currently shown on screen" (boot phase).
        # Conflating them made the question screen flash back right after
        # fetching finished (hit this in testing).
        self._prompting = False
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

    # ── Asking ──────────────────────────────────────────────

    async def on_stage_connected(self) -> None:
        """The Stage connected. Broadcasts the current state and asks if needed.

        **Waits for detection to finish.** The Stage's connection can arrive first,
        and without waiting, state would still be `unknown` and get judged as "no
        need to ask."
        """
        await self._initialized.wait()
        await self._state.publish()

        if self._prompting:
            return
        self._prompting = True
        try:
            # **One question before the per-component ones.** Being asked four times in a
            # row, each with its own size, makes the total impossible to see — and the
            # total is the number someone actually decides on.
            answer = await self._ask_for_everything()
            if answer is BulkAnswer.UNANSWERED:
                return
            if answer is BulkAnswer.DECLINED:
                # "Not now" for the whole set. **Asking the same thing again per component
                # would make the first answer meaningless.**
                return
            if answer is BulkAnswer.ALL:
                await self._install_everything()
                return
            if await self._ask_for_tts():
                return
            if await self._ask_for_stt():
                return
            await self._ask_for_embedding()
        finally:
            self._prompting = False
            self._state.asking(False)
            # Broadcasts that answering is done (or was given up on). **Never left hanging.**
            await self._state.publish()

    async def _ask_for_tts(self) -> bool:
        if self._state.snapshot.tts.state is not TtsSetupState.NOT_CONFIGURED:
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_TTS,
            install=self.install_tts_engine,
            failed=lambda: self._state.snapshot.tts.state is TtsSetupState.FAILED,
            reason=lambda: self._state.snapshot.tts.reason,
        )

    async def _ask_for_stt(self) -> bool:
        if self._state.snapshot.stt.state is not SttSetupState.NOT_CONFIGURED:
            return False
        if self._state.snapshot.stt.model is None:
            # The selected name is not pinned, so there is nothing this could fetch.
            # **A question with no good answer is worse than no question**
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_STT,
            install=self.install_speech_model,
            failed=lambda: self._state.snapshot.stt.state is SttSetupState.FAILED,
            reason=lambda: self._state.snapshot.stt.reason,
        )

    async def _ask_for_embedding(self) -> bool:
        """The embedding model (ADR-041). **Asked last, and never blocking.**

        Declining it costs similarity search and nothing else, so this is the one setup
        question whose "not now" leaves Lumi fully usable — which is also why it is asked
        after the three that do not.
        """
        if self._state.snapshot.embedding.state is not EmbeddingSetupState.NOT_CONFIGURED:
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_EMBEDDING,
            install=self.install_embedding_model,
            failed=lambda: self._state.snapshot.embedding.state is EmbeddingSetupState.FAILED,
            reason=lambda: self._state.snapshot.embedding.reason,
        )

    def _missing_components(self) -> list[MissingComponent]:
        """What is not on this machine yet, with what each would cost to fetch.

        **Ollama itself is never in here.** Lumi does not install it (ADR-023), so listing
        it under a button that fetches things would promise something this cannot do.
        """
        missing: list[MissingComponent] = []
        if self._state.snapshot.tts.state is TtsSetupState.NOT_CONFIGURED:
            missing.append(
                MissingComponent(
                    component=COMPONENT_TTS,
                    name=AIVISSPEECH_ENGINE.display_name,
                    size_bytes=AIVISSPEECH_ENGINE.size,
                )
            )
        stt_artifact = selected_stt_artifact(self._env)
        if (
            self._state.snapshot.stt.state is SttSetupState.NOT_CONFIGURED
            and stt_artifact is not None
        ):
            missing.append(
                MissingComponent(
                    component=COMPONENT_STT,
                    name=stt_artifact.name,
                    size_bytes=stt_artifact.size,
                )
            )
        # **Only when Ollama is there to fetch it into.** Offering to download a 6.6 GB
        # model that nothing can receive is worse than not offering.
        # **One lookup, and no `assert` to narrow it.** `assert` disappears under `-O`,
        # which would turn a missing model into an `AttributeError` in the one build where
        # nobody is watching.
        recommended = self._recommended_llm()
        if (
            self._state.snapshot.llm.state is LlmSetupState.MODEL_MISSING
            and recommended is not None
        ):
            missing.append(
                MissingComponent(
                    component=COMPONENT_LLM_MODEL,
                    name=recommended.display_name,
                    size_bytes=recommended.size_bytes,
                )
            )
        if self._state.snapshot.embedding.state is EmbeddingSetupState.NOT_CONFIGURED:
            missing.append(
                MissingComponent(
                    component=COMPONENT_EMBEDDING,
                    name=HARRIER_OSS_V1_270M.name,
                    size_bytes=HARRIER_OSS_V1_270M.size,
                )
            )
        return missing

    def _recommended_llm(self) -> OllamaModelArtifact | None:
        return OLLAMA_MODELS.get(self._state.snapshot.llm.model or "", QWEN_35_9B)

    async def _ask_for_everything(self) -> BulkAnswer:
        """Offers to fetch everything missing at once, with the total.

        **Skipped when there is nothing to fetch**, rather than asked and answered with an
        empty list — a consent dialog for zero bytes teaches people to dismiss consent
        dialogs.

        The answer is one of three, and "individually" is not the same as "no": it means
        the user wants to decide per item, which is what the existing per-component
        questions are for.
        """
        missing = self._missing_components()
        if not missing:
            return BulkAnswer.INDIVIDUALLY
        result = await self._prompter.ask(
            {
                "component": COMPONENT_ALL,
                "retry": False,
                "reason": None,
                "items": [item.to_payload() for item in missing],
                "total_bytes": sum(item.size_bytes for item in missing),
            }
        )
        if result is None:
            log.info("setup.prompt.unanswered", component=COMPONENT_ALL)
            return BulkAnswer.UNANSWERED

        choice = result.payload.get("choice") if result.ok else None
        log.info("setup.prompt.answered", component=COMPONENT_ALL, choice=choice)
        if choice == CHOICE_INSTALL:
            return BulkAnswer.ALL
        if choice == CHOICE_INDIVIDUALLY:
            return BulkAnswer.INDIVIDUALLY
        # **Anything unrecognised is "not now"** (fail-closed): nothing is fetched.
        return BulkAnswer.DECLINED

    async def _install_everything(self) -> None:
        """Fetches everything the bulk question listed, in order.

        **A failure does not stop the rest.** Each component reports its own state, and
        abandoning the remaining three because one distributor had a bad minute would turn
        one retry into four.
        """
        for item in self._missing_components():
            component = item.component
            if component == COMPONENT_TTS:
                await self.install_tts_engine()
            elif component == COMPONENT_STT:
                await self.install_speech_model()
            elif component == COMPONENT_LLM_MODEL:
                recommended = self._recommended_llm()
                if recommended is not None:
                    await self.install_llm_model(recommended)
            elif component == COMPONENT_EMBEDDING:
                await self.install_embedding_model()

    async def _ask_and_maybe_install(
        self,
        *,
        component: str,
        install: Callable[[], Awaitable[None]],
        failed: Callable[[], bool],
        reason: Callable[[], str | None],
    ) -> bool:
        """Asks, and fetches if chosen. **After a failure, offers the choice again.**

        The loop only turns when the user chose to fetch *and* the fetch failed, so
        **every repetition is one the user asked for** — which is why there is no cap on
        it (ADR-034). Most fetch failures are transient (a dropped connection, the
        distributor having a bad minute), and without a retry the only way to use a
        connection that already came back is to restart Lumi.

        "Not now" ends it here. The state stays `not_configured` / `failed` — **never
        reverted to "not yet attempted"** — and the boot phase becomes `blocked`, which
        is what puts the missing pieces and their fixes on screen.

        Returns `True` when the prompt could not be answered, so the caller can stop the
        component sequence. A user answer, including "not now", returns `False`.
        """
        retry = False
        detail: str | None = None

        while True:
            # **Broadcasts the phase before showing the question.** Forgetting this
            # would leave the Stage showing a loading indicator while the question
            # sits hidden behind it.
            result = await self._prompter.ask(
                {"component": component, "retry": retry, "reason": detail}
            )
            if result is None:
                # Nobody is there to answer. **Stop asking** — the next start asks again.
                log.info("setup.prompt.unanswered", component=component)
                return True

            choice = result.payload.get("choice") if result.ok else None
            # **An unrecognized answer is treated the same as "not now"** (fail-closed).
            chose_install = choice == CHOICE_INSTALL
            log.info(
                "setup.prompt.answered", component=component, choice=choice, install=chose_install
            )

            if not chose_install:
                return False

            await install()
            if not failed():
                return False

            # **A failure is never smoothed over into "done".** It goes back to the user
            # with its reason and the same two choices.
            retry = True
            detail = reason()

    # ── Fetching ──────────────────────────────────────────────

    async def install_tts_engine(self) -> None:
        await self._acquire.tts_engine()

    async def install_embedding_model(self) -> None:
        await self._acquire.embedding_model()

    async def install_speech_model(self) -> None:
        await self._acquire.speech_model()
