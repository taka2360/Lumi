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

from lumi import logging as lumi_logging
from lumi import paths
from lumi import settings as settings_module
from lumi.agent.inspector import InspectorPublisher
from lumi.agent.reactive import ReactiveLoop
from lumi.agent.session import Session
from lumi.agent.tasks import report_task_exit
from lumi.agent.warmup import warm_all
from lumi.audio.devices import AudioPlan
from lumi.audio.io import AudioIO
from lumi.character import ExpressionIntent
from lumi.content.pack import CharacterPack, ContentPackError, load_character
from lumi.kernel.arbiter import AttentionArbiter
from lumi.kernel.event import EventBus
from lumi.kernel.hooks import HookRegistry
from lumi.permission.grants import GrantStore
from lumi.permission.kernel import PermissionKernel
from lumi.permission.scope import ScopeLane
from lumi.permission.verifiers import CharacterBindVerifier, CharacterCanonicalizer
from lumi.providers.llm.base import LLMOptions
from lumi.providers.llm.ollama import OllamaProvider
from lumi.providers.registry import ProviderRegistry
from lumi.providers.stt.faster_whisper import FasterWhisperProvider
from lumi.providers.tts.provider import AivisSpeechProvider
from lumi.setup.coordinator import SetupCoordinator
from lumi.storage.audit import SqliteAuditLog
from lumi.storage.events import SqliteEventStore
from lumi.storage.sqlite import Database
from lumi.tools.builtin.character import SetExpressionTool
from lumi.tools.registry import ToolRegistry
from lumi.transport.methods import (
    METHOD_EXPRESSION,
    METHOD_MODEL,
    METHOD_SETTINGS,
    METHOD_SETTINGS_UPDATE,
)
from lumi.transport.protocol import Role
from lumi.transport.server import RequestRefused, WsServer

log = lumi_logging.get_logger(__name__)

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

        self._database = _open_event_store()

        bus = EventBus(SqliteEventStore(self._database))
        tools = self._build_tools(server, bus)

        # **Constructed here, started in `start()`** (creating the idle Activity publishes a
        # DomainEvent, which needs a running loop). Startup sequence step 9
        # → docs/architecture/core.md §7
        self._arbiter = AttentionArbiter(bus)

        #: Held because `_register_providers` needs the voice. **Which speaker Lumi uses is
        #: the Content Pack's decision**, and the Provider has to know it at `load()` time to
        #: initialize that voice rather than a different one
        self._pack = _load_pack()
        self._loop = self._build_loop(server, tools)

        # **Subscribed, not called from the Arbiter.** The Arbiter does not know the Stage
        # exists, and the send must stay off the barge-in path (`agent/inspector.py`)
        self._inspector = InspectorPublisher(
            self._arbiter, server, lambda: self._loop.last_latency if self._loop else None
        )
        bus.subscribe(self._inspector.on_event)

        # **The only inbound route in Phase 1** (ADR-028). Registering is what makes it
        # reachable at all — anything unregistered is answered `unknown_method`
        server.on_request(METHOD_SETTINGS_UPDATE, self._update_settings)

    def _build_tools(self, server: WsServer, bus: EventBus) -> ToolRegistry:
        """Every tool Lumi has, behind the Permission Kernel.

        **There is no second way in** (Invariant 2). `character.set_expression` is L0 and
        effectively passes straight through, but it is registered here and invoked through
        `ToolRegistry.invoke` like anything else — **the path is the same as production's**,
        which is the only way it can be trusted once L3 tools exist.
        """
        tools = ToolRegistry(
            PermissionKernel(GrantStore(), SqliteAuditLog(self._database)),
            bus,
            HookRegistry(),
            canonicalizers={ScopeLane.CHARACTER: CharacterCanonicalizer()},
            bind_verifiers={ScopeLane.CHARACTER: CharacterBindVerifier()},
        )
        tools.register(SetExpressionTool(_expression_sender(server)))
        return tools

    def _build_loop(self, server: WsServer, tools: ToolRegistry) -> ReactiveLoop | None:
        """The conversation loop, or `None` when there is no persona to run it as.

        **Lumi without a Content Pack isn't Lumi**, so nothing is assembled in its place.
        Startup still completes and the Stage is told why (`_announce_model`) — a Core that
        refused to start would leave nobody to say what was wrong.
        """
        if self._pack is None:
            return None
        return ReactiveLoop(
            arbiter=self._arbiter,
            providers=self._providers,
            tools=tools,
            pack=self._pack,
            notifier=server,
            options=LLMOptions(model=self._model),
            session=Session(),
            audio=self._audio,
            tts_speed=float(self._settings.tts_speed.value),
        )

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
            warm_all(
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
        self._warmup.add_done_callback(report_task_exit("warmup.crashed"))

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
        self._task.add_done_callback(
            report_task_exit("reactive.crashed", on_return="reactive.stopped")
        )
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


def _open_event_store() -> Database:
    """The DB the EventBus persists into. **In memory, deliberately.**

    **Where events live is not decided here.** Deciding where requires deciding what is
    allowed to be written, which is what Phase 2's `contracts/privacy.md` will settle
    (roadmap Phase 2, item 7). **Don't pin down a location first and attach meaning to it
    later** — a file on disk from Phase 1 would become a retention policy nobody chose.
    """
    database = Database.open(":memory:")
    database.migrate()
    log.info("core.event_store.in_memory", reason="privacy contract is Phase 2")
    return database


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
