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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace

from lumi import logging as lumi_logging
from lumi import paths, settings
from lumi.setup.detect import detect_engines, detect_ollama
from lumi.setup.engines import AIVISSPEECH_ENGINE
from lumi.setup.install import (
    ProgressCallback,
    SetupError,
    install_engine,
    install_stt_model,
    is_model_installed,
)
from lumi.setup.models import FASTER_WHISPER_LARGE_V3_TURBO, STT_MODELS, ModelArtifact
from lumi.setup.state import (
    BootPhase,
    EngineRuntime,
    LlmSetup,
    LlmSetupState,
    SetupSnapshot,
    SttSetup,
    SttSetupState,
    TtsSetup,
    TtsSetupState,
    boot_phase,
)
from lumi.transport.methods import (
    CHOICE_INSTALL,
    COMPONENT_STT,
    COMPONENT_TTS,
    METHOD_SETUP_PROMPT,
    METHOD_SETUP_STATE,
)
from lumi.transport.protocol import Role
from lumi.transport.server import NotConnectedError, WsServer

log = lumi_logging.get_logger(__name__)

#: How long to wait for the user's choice. **Human time**, so it's long.
PROMPT_TIMEOUT_S = 600.0

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


class SetupCoordinator:
    def __init__(self, server: WsServer, env: Mapping[str, str]) -> None:
        self._server = server
        self._env = env
        self._snapshot = SetupSnapshot()
        # **Kept as two separate flags.** `_prompting` is "is this sequence
        # currently in progress" (prevents duplicate runs); `_awaiting_answer` is
        # "is a question currently shown on screen" (boot phase). Conflating them
        # made the question screen flash back right after fetching finished (hit
        # this in testing).
        self._prompting = False
        self._awaiting_answer = False
        # **The Stage can connect before detection finishes.** Observed connecting
        # first in practice (2026-08-15). Deciding whether to prompt while state is
        # still unknown would skip asking when it should.
        self._initialized = asyncio.Event()

    @property
    def state(self) -> SetupSnapshot:
        return self._snapshot

    @property
    def boot(self) -> BootPhase:
        """The phase the Stage is currently being shown. **The same derivation, not a
        second one** — `_broadcast` and this read the one pure function, so what the
        runtime acts on and what the user sees can never disagree (ADR-034).
        """
        return boot_phase(self._snapshot, prompting=self._awaiting_answer)

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
        self._snapshot = SetupSnapshot(
            tts=await self._detect_tts(),
            llm=await self._detect_llm(),
            stt=await asyncio.to_thread(self._detect_stt),
        )
        await self._broadcast()

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
            return LlmSetup(
                state=LlmSetupState.NOT_CONFIGURED, reason="Ollama not found", model=None
            )
        return LlmSetup(
            state=LlmSetupState.DETECTED,
            runtime=EngineRuntime.READY if found.running else EngineRuntime.STOPPED,
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

    # ── Broadcasting ──────────────────────────────────────────────

    async def _update(
        self,
        *,
        tts: TtsSetup | None = None,
        llm: LlmSetup | None = None,
        stt: SttSetup | None = None,
    ) -> None:
        """Replaces one component and broadcasts. **The single exit point for state changes.**"""
        self._snapshot = SetupSnapshot(
            tts=tts if tts is not None else self._snapshot.tts,
            llm=llm if llm is not None else self._snapshot.llm,
            stt=stt if stt is not None else self._snapshot.stt,
        )
        await self._broadcast()

    async def _broadcast(self) -> None:
        """Broadcasts the current state. **Includes the boot phase** (docs/architecture/ui.md).

        The phase also depends on "is a question currently being asked," so
        **it's rebroadcast whenever asking starts / finishes**, even if the state
        itself hasn't changed.
        """
        payload = self._snapshot.to_payload(prompting=self._awaiting_answer)
        log.info("setup.state", **payload)
        await self._server.notify(Role.STAGE, METHOD_SETUP_STATE, payload)

    async def set_runtime(self, runtime: EngineRuntime) -> None:
        """The TTS engine **process**'s state changed.

        A separate axis from installation state, but it's the same single state
        distributed to the Stage, so **broadcasting is consolidated to this one
        exit point** (sending from two places couldn't guarantee ordering).
        """
        await self._update(tts=replace(self._snapshot.tts, runtime=runtime))

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
        await self._update(llm=LlmSetup(state=state, model=model, reason=reason, runtime=runtime))

    async def set_stt_runtime(self, runtime: EngineRuntime) -> None:
        """The STT Provider's load state changed without changing acquisition state.

        `state: failed` means fetching or verification failed. CTranslate2 failing to load
        an installed model is instead `state: installed × runtime: failed` (ADR-035).
        """
        await self._update(stt=replace(self._snapshot.stt, runtime=runtime))

    # ── Asking ──────────────────────────────────────────────

    async def on_stage_connected(self) -> None:
        """The Stage connected. Broadcasts the current state and asks if needed.

        **Waits for detection to finish.** The Stage's connection can arrive first,
        and without waiting, state would still be `unknown` and get judged as "no
        need to ask."
        """
        await self._initialized.wait()
        await self._broadcast()

        if self._prompting:
            return
        self._prompting = True
        try:
            if await self._ask_for_tts():
                return
            await self._ask_for_stt()
        finally:
            self._prompting = False
            self._awaiting_answer = False
            # Broadcasts that answering is done (or was given up on). **Never left hanging.**
            await self._broadcast()

    async def _ask_for_tts(self) -> bool:
        if self._snapshot.tts.state is not TtsSetupState.NOT_CONFIGURED:
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_TTS,
            install=self.install_tts_engine,
            failed=lambda: self._snapshot.tts.state is TtsSetupState.FAILED,
            reason=lambda: self._snapshot.tts.reason,
        )

    async def _ask_for_stt(self) -> bool:
        if self._snapshot.stt.state is not SttSetupState.NOT_CONFIGURED:
            return False
        if self._snapshot.stt.model is None:
            # The selected name is not pinned, so there is nothing this could fetch.
            # **A question with no good answer is worse than no question**
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_STT,
            install=self.install_speech_model,
            failed=lambda: self._snapshot.stt.state is SttSetupState.FAILED,
            reason=lambda: self._snapshot.stt.reason,
        )

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
            self._awaiting_answer = True
            await self._broadcast()
            try:
                result = await self._server.invoke(
                    Role.STAGE,
                    METHOD_SETUP_PROMPT,
                    {"component": component, "retry": retry, "reason": detail},
                    timeout=PROMPT_TIMEOUT_S,
                )
            except (NotConnectedError, TimeoutError):
                # Nobody is there to answer. **Stop asking** — the next start asks again.
                log.info("setup.prompt.unanswered", component=component)
                self._awaiting_answer = False
                return True

            # **The question disappears the moment an answer arrives.** Never lets the question
            # paint over the fetching phase.
            self._awaiting_answer = False
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

    def _throttled(self, report: ProgressCallback) -> ProgressCallback:
        """Broadcasts progress at most every 1%. **Sending on every chunk would flood the WS.**

        **Broadcasting always goes through `_update`.** Calling `notify` directly would mean
        choosing the flag passed to `to_payload(prompting=...)` by hand, and risk conflating
        `_prompting` (is this sequence in progress) with `_awaiting_answer` (is a question
        currently shown). Conflating them would stream `boot=setup` for the entire 200 MB
        fetch with no progress shown.
        """
        last_sent = -1.0

        async def throttled(fraction: float) -> None:
            nonlocal last_sent
            if fraction - last_sent < 0.01 and fraction < 1.0:
                return
            last_sent = fraction
            await report(fraction)

        return throttled

    async def install_tts_engine(self) -> None:
        """Fetches because the user chose to. **No external communication happens before this
        point.**
        """
        artifact = AIVISSPEECH_ENGINE
        await self._update(
            tts=TtsSetup(
                state=TtsSetupState.INSTALLING,
                engine_name=artifact.display_name,
                version=artifact.version,
                progress=0.0,
            )
        )

        async def report(fraction: float) -> None:
            await self._update(tts=replace(self._snapshot.tts, progress=fraction))

        try:
            executable = await install_engine(
                artifact, paths.engines_dir(), progress=self._throttled(report)
            )
        except SetupError as error:
            log.warning("setup.install.failed", reason=error.reason, detail=error.detail)
            await self._update(
                tts=TtsSetup(
                    state=TtsSetupState.FAILED,
                    engine_name=artifact.display_name,
                    version=artifact.version,
                    reason=error.reason,
                )
            )
            return
        except asyncio.CancelledError:
            await self._update(tts=TtsSetup(state=TtsSetupState.FAILED, reason="cancelled"))
            raise
        except Exception:
            # Even for the unexpected, **never silently reverts to not-configured.** What happened
            # is recorded.
            log.exception("setup.install.crashed")
            await self._update(tts=TtsSetup(state=TtsSetupState.FAILED, reason="unexpected_error"))
            return

        await self._update(
            tts=TtsSetup(
                state=TtsSetupState.INSTALLED,
                engine_name=artifact.display_name,
                version=artifact.version,
                port=artifact.default_port,
                executable=str(executable),
            )
        )

    async def install_speech_model(self) -> None:
        """Fetches the speech-recognition model. **The same rules as the engine**
        (pinned URL + size + SHA-256 + atomic install + rollback → setup.md §3b).
        """
        artifact = selected_stt_artifact(self._env)
        if artifact is None:
            await self._update(
                stt=SttSetup(state=SttSetupState.FAILED, model=None, reason="unpinned_model")
            )
            return
        await self._update(
            stt=SttSetup(state=SttSetupState.INSTALLING, model=artifact.name, progress=0.0)
        )

        async def report(fraction: float) -> None:
            await self._update(stt=replace(self._snapshot.stt, progress=fraction))

        try:
            await install_stt_model(
                artifact, paths.stt_models_dir(), progress=self._throttled(report)
            )
        except SetupError as error:
            log.warning("setup.model.failed", reason=error.reason, detail=error.detail)
            await self._update(
                stt=SttSetup(state=SttSetupState.FAILED, model=artifact.name, reason=error.reason)
            )
            return
        except asyncio.CancelledError:
            await self._update(
                stt=SttSetup(state=SttSetupState.FAILED, model=artifact.name, reason="cancelled")
            )
            raise
        except Exception:
            log.exception("setup.model.crashed")
            await self._update(
                stt=SttSetup(
                    state=SttSetupState.FAILED, model=artifact.name, reason="unexpected_error"
                )
            )
            return

        await self._update(stt=SttSetup(state=SttSetupState.INSTALLED, model=artifact.name))
