"""The coordinator for first-run setup. **All decisions are concentrated here**
(the Stage only displays).

Design → docs/architecture/setup.md §2 / §2b

Flow:

```
Startup → detection (local only) → state is determined
Stage connects → state is broadcast
  If state is not_configured and it hasn't been asked yet → ask whether to fetch
    "Fetch" → installing (progress broadcast) → installed / failed
    "Don't fetch" → stays not_configured. **Only remembers that it was asked**
```

## Three components, asked about one at a time

TTS (engine) and STT (model) are both fetched **with consent**; the LLM is
**detected only** (ADR-023). Questions are asked in sequence, never at once:
two consent dialogs on a first run is how a program teaches people to click
through without reading.

**The order is TTS first.** Being unable to speak is the more visible failure,
and it is the one whose fetch the boot screen already waits on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path

from lumi import logging as lumi_logging
from lumi import paths
from lumi.setup.detect import detect_engines, detect_ollama
from lumi.setup.engines import AIVISSPEECH_ENGINE
from lumi.setup.install import (
    ProgressCallback,
    SetupError,
    install_engine,
    install_stt_model,
    is_model_installed,
)
from lumi.setup.models import FASTER_WHISPER_SMALL, ModelArtifact
from lumi.setup.state import (
    EngineRuntime,
    LlmSetup,
    LlmSetupState,
    SetupAnswers,
    SetupSnapshot,
    SttSetup,
    SttSetupState,
    TtsSetup,
    TtsSetupState,
)
from lumi.transport.protocol import Role
from lumi.transport.server import NotConnectedError, WsServer

log = lumi_logging.get_logger(__name__)

#: How long to wait for the user's choice. **Human time**, so it's long.
PROMPT_TIMEOUT_S = 600.0

#: The cap on how many times to ask (including retries after a failure). **Never nags.**
MAX_PROMPTS = 2

#: The method for broadcasting state (Core → Stage).
METHOD_STATE = "stage.setup.state"
#: The method for asking whether to fetch (Core → Stage, awaits the result).
METHOD_PROMPT = "stage.setup.prompt"

#: The choices the Stage returns in `result`. **A value that goes on the wire**, so
#: docs/contracts/wire.json is authoritative (→ ADR-022). `CHOICE_SKIP` isn't used in
#: any comparison, but it's kept here so **only one side of the contract isn't documented**.
CHOICE_INSTALL = "install"
CHOICE_SKIP = "skip"

#: Which component a question is about. **The panel has to say what it is fetching** —
#: "may I download this?" without a subject is not consent
COMPONENT_TTS = "tts"
COMPONENT_STT = "stt"

#: The speech-recognition model Lumi fetches. **Pinned** (docs/architecture/setup.md §3b);
#: a different `LUMI_STT_MODEL` is not something this offers to fetch
STT_ARTIFACT: ModelArtifact = FASTER_WHISPER_SMALL


class SetupCoordinator:
    def __init__(self, server: WsServer, env: Mapping[str, str]) -> None:
        self._server = server
        self._env = env
        self._snapshot = SetupSnapshot()
        self._answers_path: Path = paths.setup_state_file()
        self._answers = SetupAnswers()
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

    async def initialize(self) -> None:
        """Called exactly once at startup. **No external communication.**"""
        try:
            self._answers = await asyncio.to_thread(SetupAnswers.load, self._answers_path)
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
                state=LlmSetupState.NOT_CONFIGURED, reason="Ollama が見つからない", model=None
            )
        return LlmSetup(
            state=LlmSetupState.DETECTED,
            runtime=EngineRuntime.READY if found.running else EngineRuntime.STOPPED,
        )

    def _detect_stt(self) -> SttSetup:
        """A file check, not a process check. **Never touches the network** — a half-fetched
        directory reads as not-installed (`is_model_installed` checks every pinned size).
        """
        installed = is_model_installed(STT_ARTIFACT, paths.stt_models_dir())
        return SttSetup(
            state=SttSetupState.INSTALLED if installed else SttSetupState.NOT_CONFIGURED,
            model=STT_ARTIFACT.name,
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
        await self._server.notify(Role.STAGE, METHOD_STATE, payload)

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
            await self._ask_for_tts()
            await self._ask_for_stt()
        finally:
            self._prompting = False
            self._awaiting_answer = False
            # Broadcasts that answering is done (or was given up on). **Never left hanging.**
            await self._broadcast()

    async def _ask_for_tts(self) -> None:
        if self._snapshot.tts.state is not TtsSetupState.NOT_CONFIGURED:
            return
        if self._answers.tts_prompt_answered:
            # **Once answered, never asked again.** Asking every startup is the textbook definition
            # of annoying.
            return
        await self._ask_and_maybe_install(
            component=COMPONENT_TTS,
            install=self.install_tts_engine,
            failed=lambda: self._snapshot.tts.state is TtsSetupState.FAILED,
            reason=lambda: self._snapshot.tts.reason,
            remember=lambda answers: replace(answers, tts_prompt_answered=True),
        )

    async def _ask_for_stt(self) -> None:
        if self._snapshot.stt.state is not SttSetupState.NOT_CONFIGURED:
            return
        if self._answers.stt_prompt_answered:
            return
        await self._ask_and_maybe_install(
            component=COMPONENT_STT,
            install=self.install_speech_model,
            failed=lambda: self._snapshot.stt.state is SttSetupState.FAILED,
            reason=lambda: self._snapshot.stt.reason,
            remember=lambda answers: replace(answers, stt_prompt_answered=True),
        )

    async def _ask_and_maybe_install(
        self,
        *,
        component: str,
        install: Callable[[], Awaitable[None]],
        failed: Callable[[], bool],
        reason: Callable[[], str | None],
        remember: Callable[[SetupAnswers], SetupAnswers],
    ) -> None:
        """Asks, and fetches if chosen. **Asks again exactly once if it fails.**

        Only one retry. Beyond that, the annoyance outweighs the benefit.
        State stays `failed` since it's never reverted to "not yet attempted."
        """
        retry = False
        detail: str | None = None

        for _ in range(MAX_PROMPTS):
            # **Broadcasts the phase before showing the question.** Forgetting this
            # would leave the Stage showing a loading indicator while the question
            # sits hidden behind it.
            self._awaiting_answer = True
            await self._broadcast()
            try:
                result = await self._server.invoke(
                    Role.STAGE,
                    METHOD_PROMPT,
                    {"component": component, "retry": retry, "reason": detail},
                    timeout=PROMPT_TIMEOUT_S,
                )
            except (NotConnectedError, TimeoutError):
                # No answer was received. **Never counted as "asked"** (asked again next startup).
                log.info("setup.prompt.unanswered", component=component)
                return

            # **The question disappears the moment an answer arrives.** Never lets the question
            # paint over the fetching phase.
            self._awaiting_answer = False
            choice = result.payload.get("choice") if result.ok else None
            # **An unrecognized answer is treated the same as "don't fetch"** (fail-closed).
            chose_install = choice == CHOICE_INSTALL
            log.info(
                "setup.prompt.answered", component=component, choice=choice, install=chose_install
            )

            self._answers = remember(self._answers)
            await asyncio.to_thread(self._answers.save, self._answers_path)

            if not chose_install:
                return

            await install()
            if not failed():
                return

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
        artifact = STT_ARTIFACT
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
