"""Assembles what a conversation needs. **Wiring only. Holds no judgment.**

Startup sequence → docs/architecture/core.md §7

Separated out from `__main__` so **"what connects to what" can be read on one screen**.
This replaces Phase 0's `Greeter`; `greeting.py` was absorbed into here and no longer exists.

## Not-yet-set-up is not "broken"

Ollama not installed / no STT model yet / no input device —
all of these are **normal states**, and startup continues while making that explicit
(ADR-023 / docs/architecture/setup.md). **Never silently degrade**, but never block startup either.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from lumi.character import METHOD_EXPRESSION, ExpressionIntent
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
from lumi.setup.state import EngineRuntime, LlmSetupState
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

        **Takes effect on the next start, and says so.** Swapping a loaded model out from
        under a turn is Phase 5's `ModelResourceManager` problem; pretending it applied
        now would make the displayed value a lie.
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

        payload_out = self._settings.to_payload()
        await self._server.notify(Role.STAGE, METHOD_SETTINGS, payload_out)
        return {"applied_at_next_start": True}

    @property
    def arbiter(self) -> AttentionArbiter:
        """**Read-only.** What the Inspector reads the Activity tree from (roadmap Phase 1).
        Transitions stay the Arbiter's alone (Invariant 4).
        """
        return self._arbiter

    async def start(self) -> None:
        """**Starts regardless of whether it can speak.** Missing pieces show up in logs/state."""
        self._register_providers()
        # **Before anything can arbitrate.** `current()` raises while foreground is unset, so
        # without this the first VAD event kills the reactive loop and Lumi goes deaf for the
        # rest of the session (observed 2026-08-17).
        await self._arbiter.start()
        self._inspector.start()
        # **Sent once at startup.** The Stage shows values, not judgments — including
        # which of them an environment variable is currently overriding
        await self._server.notify(Role.STAGE, METHOD_SETTINGS, self._settings.to_payload())
        await self._audio.start()
        # **The engine is started here, not at the first utterance.** The boot phase the
        # Stage shows is derived from this process state, so with nobody starting it and
        # reporting back, `installed × stopped` keeps rendering "starting" forever
        # (docs/architecture/ui.md "Boot phases"). Deferring it to first speech would also
        # add the engine's startup — tens of seconds, minutes on the first run — onto the
        # first reply.
        #
        # **Not awaited.** Listening and the reactive loop don't depend on the engine, and
        # blocking here would hold up capture for the whole startup.
        self._warmup = asyncio.create_task(
            _warm(self._providers, self._setup, self._model), name="warmup"
        )

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
        if tts.usable and tts.port is not None:
            executable = Path(tts.executable) if tts.executable else None
            self._providers.register(AivisSpeechProvider(tts.port, executable=executable))
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
            with contextlib.suppress(asyncio.CancelledError):
                await task
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


async def _warm(providers: ProviderRegistry, setup: SetupCoordinator, model: str) -> None:
    """Brings the engines up and **reports what each one turned out to be.**

    Sequential, not concurrent: the TTS engine saturates the GPU while it starts, and
    probing the LLM in the middle of that measures the contention rather than the LLM.
    """
    await warm_tts(providers, setup)
    await warm_llm(providers, setup, model)


async def warm_llm(providers: ProviderRegistry, setup: SetupCoordinator, model: str) -> None:
    """Finds out whether the LLM can actually answer, and says so.

    **Detection alone cannot see `model_missing`** — that needs Ollama's API, which only
    the Provider talks to (docs/architecture/setup.md §2b). So the state is settled here,
    where the answer is known.

    **Never blocks the character.** No LLM means a Lumi that listens and doesn't answer;
    the SetupPanel is what says so (docs/architecture/ui.md).
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


async def warm_tts(providers: ProviderRegistry, setup: SetupCoordinator) -> None:
    """Starts the TTS engine, and **reports the process state as it moves**.

    Two states that move independently (docs/architecture/setup.md "Never mix
    installation state and process state"): the Provider owns the process, the
    Coordinator owns the state the Stage sees. **Whoever starts the process is the one
    who has to report it**, or the two silently drift apart.

    Failing to start is **not** a reason to keep the user waiting — `failed` resolves to
    boot phase `ready`, the character comes out, and the SetupPanel is what says it's
    broken. **The character is never held hostage** (docs/architecture/ui.md).
    """
    if not providers.has(ProviderKind.TTS):
        # Not set up, or the fetch was declined. **Not broken**, so the state stays
        # `stopped` — it really isn't running, and nothing is starting it.
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
