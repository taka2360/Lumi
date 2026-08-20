"""Assembles what a conversation needs. **Wiring only. Holds no judgment.**

Startup sequence → docs/architecture/core.md §7

Separated out from `__main__` so **"what connects to what" can be read on one screen**.
This replaces Phase 0's `Greeter`; `greeting.py` was absorbed into here and no longer exists.

## Not-yet-set-up is not "broken", but it does stop Lumi from coming out

Ollama not installed / no STT model yet — these are **normal states**, not crashes, and
Core keeps running and keeps saying so. What they do *not* do is let the character
appear: hearing, thinking and speaking are what a conversation is made of, so the boot
phase stays `blocked` until all three work (ADR-034).

**Assembly still completes either way.** Providers are registered, the engine is warmed
and what each one turned out to be is reported — that reporting is exactly what decides
whether the phase can leave `blocked`. What is gated is the microphone and the reactive
loop (ADR-033), not the wiring.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from lumi import logging as lumi_logging
from lumi import paths
from lumi import settings as settings_module
from lumi.agent.inspector import InspectorPublisher
from lumi.agent.reactive import ReactiveLoop
from lumi.agent.session import Session
from lumi.audio.devices import AudioPlan
from lumi.audio.io import AudioIO
from lumi.character import METHOD_EXPRESSION, METHOD_MODEL, ExpressionIntent
from lumi.content.pack import CharacterPack, ContentPackError, load_character
from lumi.kernel.arbiter import AttentionArbiter
from lumi.kernel.event import EventBus
from lumi.kernel.hooks import HookRegistry
from lumi.permission.grants import GrantStore
from lumi.permission.kernel import PermissionKernel
from lumi.permission.scope import ScopeLane
from lumi.permission.verifiers import CharacterBindVerifier, CharacterCanonicalizer
from lumi.providers.base import ProviderError, ProviderKind, ProviderNotConfigured
from lumi.providers.llm.base import LLMOptions
from lumi.providers.llm.ollama import OllamaProvider
from lumi.providers.registry import ProviderRegistry
from lumi.providers.stt.faster_whisper import FasterWhisperProvider
from lumi.providers.tts.provider import AivisSpeechProvider
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.state import BootPhase, EngineRuntime, LlmSetupState, SttSetupState
from lumi.storage.audit import SqliteAuditLog
from lumi.storage.events import SqliteEventStore
from lumi.storage.sqlite import Database
from lumi.tools.builtin.character import SetExpressionTool
from lumi.tools.registry import ToolRegistry
from lumi.transport.protocol import Role
from lumi.transport.server import RequestRefused, WsServer

log = lumi_logging.get_logger(__name__)

#: Core → Stage. The effective settings and **where each value came from**
#: (contract → docs/contracts/wire.json)
METHOD_SETTINGS: Final = "stage.settings.state"

#: Stage → Core. **The one method registered as inbound in Phase 1** (ADR-028).
#: The Stage asks; Core validates, decides, writes, and broadcasts the result
METHOD_SETTINGS_UPDATE: Final = "stage.settings.update"

#: What is configurable, and each key's environment override and default, live in
#: `lumi.settings.KEYS`. **Declared once** — a second copy here drifted the moment the
#: STT model changed (docs/DESIGN.md §12).


class ConversationRuntime:
    """Holds what conversation needs and owns start/stop. **Not disposable** (lives as long as the
    process).
    """

    __slots__ = (
        "_arbiter",
        "_audio",
        "_database",
        "_inspector",
        "_loop",
        "_model",
        "_pack",
        "_providers",
        "_server",
        "_settings",
        "_setup",
        "_task",
        "_warmup",
    )

    def __init__(self, server: WsServer, setup: SetupCoordinator, plan: AudioPlan) -> None:
        self._server = server
        self._setup = setup
        # **Read once, at startup.** A setting that changed mid-session while the model
        # stayed loaded would make the displayed value a lie (applying it is Phase 2's
        # `ModelResourceManager` problem, not something to fake here)
        self._settings = settings_module.load(paths.settings_file())
        self._model = self._settings.llm_model.value
        self._task: asyncio.Task[None] | None = None
        self._warmup: asyncio.Task[None] | None = None
        self._audio = AudioIO(plan)
        self._providers = ProviderRegistry()

        # **The location for events is not decided here.** Deciding where requires
        # deciding "what's allowed to be written," which is what Phase 2's
        # contracts/privacy.md will settle. **Don't pin down a location first and
        # attach meaning to it later.**
        self._database = Database.open(":memory:")
        self._database.migrate()
        log.info("core.event_store.in_memory", reason="privacy contract is Phase 2")

        bus = EventBus(SqliteEventStore(self._database))
        tools = ToolRegistry(
            PermissionKernel(GrantStore(), SqliteAuditLog(self._database)),
            bus,
            HookRegistry(),
            canonicalizers={ScopeLane.CHARACTER: CharacterCanonicalizer()},
            bind_verifiers={ScopeLane.CHARACTER: CharacterBindVerifier()},
        )
        tools.register(SetExpressionTool(_expression_sender(server)))

        # **Constructed here, started in `start()`** (creating the idle Activity publishes a
        # DomainEvent, which needs a running loop). Startup sequence step 9
        # → docs/architecture/core.md §7
        self._arbiter = AttentionArbiter(bus)

        pack = _load_pack()
        #: Held because `_register_providers` needs the voice. **Which speaker Lumi uses is
        #: the Content Pack's decision**, and the Provider has to know it at `load()` time to
        #: initialize that voice rather than a different one
        self._pack = pack
        self._loop = (
            None
            if pack is None
            else ReactiveLoop(
                arbiter=self._arbiter,
                providers=self._providers,
                tools=tools,
                pack=pack,
                notifier=server,
                options=LLMOptions(model=self._model),
                session=Session(),
                audio=self._audio,
                tts_speed=float(self._settings.tts_speed.value),
            )
        )

        # **Subscribed, not called from the Arbiter.** The Arbiter does not know the Stage
        # exists, and the send must stay off the barge-in path (`agent/inspector.py`)
        self._inspector = InspectorPublisher(
            self._arbiter, server, lambda: self._loop.last_latency if self._loop else None
        )
        bus.subscribe(self._inspector.on_event)

        # **The only inbound route in Phase 1** (ADR-028). Registering is what makes it
        # reachable at all — anything unregistered is answered `unknown_method`
        server.on_request(METHOD_SETTINGS_UPDATE, self._update_settings)

    async def _update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        """The Stage asked to change a setting. **Core validates and decides** (ADR-028).

        Model/device changes take effect on the next start. Locale is presentation-only,
        so the settings notification applies it immediately without touching a running turn.
        Swapping a loaded model out is Phase 5's `ModelResourceManager` problem.
        """
        changes = payload.get("changes")
        if not isinstance(changes, dict):
            raise RequestRefused("invalid_payload")
        readable = all(
            isinstance(key, str) and isinstance(value, str) for key, value in changes.items()
        )
        if not readable:
            raise RequestRefused("invalid_payload")

        try:
            self._settings = await asyncio.to_thread(
                settings_module.save, paths.settings_file(), self._settings, changes
            )
        except settings_module.SettingsError as error:
            # **The reason travels back**, never just "failed"
            raise RequestRefused(type(error).__name__) from error

        if "tts_speed" in changes and self._loop is not None:
            self._loop.set_tts_speed(float(self._settings.tts_speed.value))

        payload_out = self._settings.to_payload()
        await self._server.notify(Role.STAGE, METHOD_SETTINGS, payload_out)
        return {"applied_at_next_start": any(key not in {"locale", "tts_speed"} for key in changes)}

    async def _announce_model(self) -> None:
        """Tells the Stage **which model to draw, and the credit that goes with it** (ADR-029).

        **Sent even when there is no model** (`path: null`). "Nothing has arrived yet" and
        "this Content Pack ships no model" are different states, and only the second one
        should put the placeholder on screen with a reason (docs/architecture/ui.md).

        An absolute path, not a URL — **Core does not serve files** and does not know how
        Shell addresses them.
        """
        model = self._pack.model if self._pack else None
        if model is None:
            reason = (
                "Content Pack がモデルを含んでいない" if self._pack else "Content Pack が読めない"
            )
            await self._server.notify(Role.STAGE, METHOD_MODEL, {"path": None, "reason": reason})
            log.info("character.model.absent", reason=reason)
            return
        await self._server.notify(Role.STAGE, METHOD_MODEL, model.to_payload())
        log.info("character.model", path=str(model.path), format=model.format)

    @property
    def arbiter(self) -> AttentionArbiter:
        """**Read-only.** What the Inspector reads the Activity tree from (roadmap Phase 1).
        Transitions stay the Arbiter's alone (Invariant 4).
        """
        return self._arbiter

    async def start(self) -> None:
        """**Assembles regardless of what is missing.** What is missing shows up in state,
        and decides whether the character is allowed out (ADR-034).
        """
        self._register_providers()
        # **Before anything can arbitrate.** `current()` raises while foreground is unset, so
        # without this the first VAD event kills the reactive loop and Lumi goes deaf for the
        # rest of the session (observed 2026-08-17).
        await self._arbiter.start()
        self._inspector.start()
        # **Sent once at startup.** The Stage shows values, not judgments — including
        # which of them an environment variable is currently overriding
        await self._server.notify(Role.STAGE, METHOD_SETTINGS, self._settings.to_payload())
        await self._announce_model()
        # **The engine is started here, not at the first utterance.** The boot phase the
        # Stage shows is derived from this process state, so with nobody starting it and
        # reporting back, `installed × stopped` keeps rendering "starting" forever
        # (docs/architecture/ui.md "Boot phases"). Deferring it to first speech would also
        # add the engine's startup — tens of seconds, minutes on the first run — onto the
        # first reply.
        #
        # **Not awaited.** The Stage connection handler must stay responsive while the engine
        # starts. `_warm` opens audio and starts the reactive loop only once all three have
        # reported and the phase has actually reached `ready` (ADR-033 / ADR-034).
        self._warmup = asyncio.create_task(
            _warm(
                self._providers,
                self._setup,
                self._model,
                on_ready=self._start_listening,
            ),
            name="warmup",
        )
        # **Nobody awaits this task while it runs.** Without a callback an unexpected failure
        # stays invisible until GC, and `stop()` is where it would finally surface — as an
        # exception that aborts the rest of shutdown
        self._warmup.add_done_callback(_report_warmup_exit)

    async def _start_listening(self) -> None:
        """Open voice input only after Core has broadcast `boot: ready` (ADR-033).

        **Never called while the phase is `blocked`.** A screen that says setup is
        incomplete must not be quietly listening behind itself — and with no LLM or no
        STT there is nothing that could answer anyway.
        """
        await self._audio.start()

        if self._loop is None:
            log.warning("conversation.disabled", reason="content pack")
            return
        self._task = asyncio.create_task(self._loop.run(), name="reactive")
        # **A dead reactive loop means Lumi is deaf, and nothing else notices.** asyncio only
        # surfaces an unretrieved task exception at GC, which is how the missing
        # `arbiter.start()` stayed invisible. **Never let this exit quietly.**
        self._task.add_done_callback(_report_reactive_exit)
        log.info("conversation.started", can_listen=self._audio.can_listen)

    def _register_providers(self) -> None:
        """**Registered even when not set up.** Failure happens at `load()` time, which
        is the first point where "what's missing" can be stated concretely.
        """
        tts = self._setup.state.tts
        # **Installed, not ready.** Whether the process comes up is what `warm_tts` is
        # about to find out, and it needs a registered Provider to find it out with.
        if tts.installed and tts.port is not None:
            executable = Path(tts.executable) if tts.executable else None
            self._providers.register(
                AivisSpeechProvider(
                    tts.port,
                    executable=executable,
                    # `None` = defer to the engine's default (`voice.toml` may not pin one).
                    # **The same speaker `ReactiveLoop._voice()` will ask for** — initializing
                    # a different voice than the one that speaks would warm the wrong model
                    speaker=self._pack.voice.speaker if self._pack else None,
                )
            )
        else:
            log.info("tts.not_registered", state=str(tts.state))

        self._providers.register(OllamaProvider(self._model))
        self._providers.register(
            FasterWhisperProvider(
                self._settings.stt_model.value,
                paths.stt_models_dir(),
            )
        )

    async def stop(self) -> None:
        """**Only stops what Lumi itself started** (docs/architecture/core.md §6)."""
        for task in (self._warmup, self._task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # It already failed before the cancel landed. **Shutdown continues** — the
                # steps below release a device, an engine process and a DB handle, and none
                # of them may be skipped because a warmup raised (it is logged, not swallowed)
                log.exception("conversation.task_failed_on_stop", task=task.get_name())
        self._warmup = None
        self._task = None
        await self._inspector.stop()
        await self._audio.stop()
        try:
            async with asyncio.timeout(30.0):
                await self._providers.unload_all()
        except TimeoutError:
            log.warning("providers.unload_timeout")
        self._database.close()


def _report_warmup_exit(task: asyncio.Task[None]) -> None:
    """**A warmup that died is a cold engine nobody is going to notice.**

    Cancellation is normal shutdown. Anything else means the first reply pays the load cost
    at best, and the Stage keeps showing a state nobody will move at worst.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.error("warmup.crashed", error=str(error), exc_info=error)


def _report_reactive_exit(task: asyncio.Task[None]) -> None:
    """**Being deaf is a failure, not a quiet state.**

    Normal shutdown cancels the task, which is the one exit that isn't news.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.error("reactive.crashed", error=str(error), exc_info=error)
    else:
        # `run()` returns on its own only when there is no input device
        log.info("reactive.stopped")


async def _warm(
    providers: ProviderRegistry,
    setup: SetupCoordinator,
    model: str,
    *,
    on_ready: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Brings the engines up and **reports what each one turned out to be.**

    Sequential, not concurrent: the TTS engine saturates the GPU while it starts, and
    probing the LLM in the middle of that measures the contention rather than the LLM.

    **All three, not just the two that report state.** Warming means the weights are in
    memory — anything left cold is simply a bill handed to the first reply
    (docs/interfaces/provider.md "`load()` は接続確認ではない"), and STT was the one nobody
    was warming at all.

    **Listening starts last, and only if the phase actually reached `ready`** (ADR-034).
    It used to start right after the TTS engine, back when `ready` meant "the engine is
    up"; now it means all three work, and the microphone follows that same boundary
    rather than a weaker one (ADR-033). If anything is still missing the phase is
    `blocked`, and Lumi does not listen behind a screen that says it has not started.
    """
    await warm_tts(providers, setup)
    await warm_llm(providers, setup, model)
    await warm_stt(providers, setup)

    if on_ready is None:
        return
    if setup.boot is not BootPhase.READY:
        # **The same derivation the Stage was handed**, read back rather than re-decided
        # here. Two places deciding "is it ready" is how the screen and the microphone
        # come to disagree.
        log.info("conversation.not_started", boot=str(setup.boot))
        return
    await on_ready()


async def warm_llm(providers: ProviderRegistry, setup: SetupCoordinator, model: str) -> None:
    """Finds out whether the LLM can actually answer, and says so.

    **Detection alone cannot see `model_missing`** — that needs Ollama's API, which only
    the Provider talks to (docs/architecture/setup.md §2b). So the state is settled here,
    where the answer is known.

    **This report is what lets the character out** (ADR-034). A Lumi that hears and never
    answers is not a lesser Lumi, it is a broken-looking one, so the phase stays `blocked`
    until Ollama is there, running, and holding the configured model. What is missing and
    what to do about it is on screen the whole time.
    """
    try:
        await providers.get(ProviderKind.LLM)
    except ProviderNotConfigured as error:
        # Ollama answered, but the model isn't pulled. **A different instruction entirely**
        await setup.report_llm(LlmSetupState.MODEL_MISSING, reason=error.detail, model=model)
        return
    except ProviderError as error:
        # Not running (or answering abnormally). **Never reported as "not installed"** —
        # detection already established whether it is on the machine
        log.info("llm.warmup_failed", reason=error.reason, detail=error.detail)
        await setup.report_llm(LlmSetupState.NOT_CONFIGURED, reason=error.detail, model=model)
        return
    await setup.report_llm(LlmSetupState.DETECTED, reason=None, model=model)


async def warm_stt(providers: ProviderRegistry, setup: SetupCoordinator) -> None:
    """Builds the speech-recognition model **before** the first utterance needs it.

    **This was the `unaccounted_ms`.** `stt_ms` times `transcribe`, so the 2489 ms model
    build sat between the spans and showed up only as unattributed time (2321 ms observed
    2026-08-18) — the reserve's warning light lit by something that was never a mystery.

    Unlike the LLM, whether the model is present is decided by looking at the disk, which
    detection already did (docs/architecture/setup.md §2b). An uninstalled model is therefore
    not warmed; a failure to build an installed model is reported as `runtime: failed`
    while its acquisition state stays `installed`, so it cannot make the boot phase look ready.
    """
    if setup.state.stt.state is not SttSetupState.INSTALLED:
        return
    await setup.set_stt_runtime(EngineRuntime.STARTING)
    try:
        await providers.get(ProviderKind.STT)
    except ProviderError as error:
        log.warning("stt.warmup_failed", reason=error.reason, detail=error.detail)
        await setup.set_stt_runtime(EngineRuntime.FAILED)
        return
    await setup.set_stt_runtime(EngineRuntime.READY)


async def warm_tts(providers: ProviderRegistry, setup: SetupCoordinator) -> None:
    """Starts the TTS engine, and **reports the process state as it moves**.

    Two states that move independently (docs/architecture/setup.md "Never mix
    installation state and process state"): the Provider owns the process, the
    Coordinator owns the state the Stage sees. **Whoever starts the process is the one
    who has to report it**, or the two silently drift apart.

    Failing to start resolves to boot phase `blocked`, never `ready` (ADR-034): an
    engine that is installed and will not run is broken, and **a fetch that failed must
    never be able to read as a successful start.** The screen says which of the two it
    was, and offers what to do next.
    """
    if not providers.has(ProviderKind.TTS):
        # Not set up, or the fetch was declined. **Not broken**, so the state stays
        # `stopped` — it really isn't running, and nothing is starting it. Broadcast it
        # anyway: `stopped` is what moves the phase off `starting` and onto `blocked`.
        await setup.set_runtime(EngineRuntime.STOPPED)
        return

    await setup.set_runtime(EngineRuntime.STARTING)
    try:
        await providers.get(ProviderKind.TTS)
    except ProviderError as error:
        # Installed but won't start = broken. **Never leave it looking like it's still
        # starting.**
        log.warning("tts.warmup_failed", reason=error.reason, detail=error.detail)
        await setup.set_runtime(EngineRuntime.FAILED)
        return
    await setup.set_runtime(EngineRuntime.READY)


def _load_pack() -> CharacterPack | None:
    """**Lumi without a persona isn't Lumi.** If it can't be read, conversation isn't assembled."""
    try:
        return load_character(paths.default_character_dir())
    except ContentPackError as error:
        log.error("content.unavailable", error=str(error))
        return None


def _expression_sender(server: WsServer) -> Callable[[ExpressionIntent], Awaitable[None]]:
    """Exit point for `character.set_expression`. **The Tool knows nothing about WS or
    the Stage** (it's injected).

    Sent as a notify, never a command: **Core does not wait to hear that a face changed.**
    Blocking the LLM stream on the Stage's acknowledgement would put rendering on the
    barge-in critical path.
    """

    async def send(intent: ExpressionIntent) -> None:
        await server.notify(Role.STAGE, METHOD_EXPRESSION, intent.to_payload())

    return send
