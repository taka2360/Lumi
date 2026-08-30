"""Assembles what a conversation needs. **Wiring only. Holds no judgment.**

Startup sequence → docs/architecture/core.md §7

Separated out from `__main__` so **"what connects to what" can be read on one screen**.
Warming the engines and deciding whether Lumi may come out live in `warmup.py`; **this
file only connects things together.**

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
from lumi.agent.episodes import EpisodeRecorder
from lumi.agent.inspector import InspectorPublisher
from lumi.agent.maintenance import MaintenanceJobs
from lumi.agent.prompt import estimate_tokens
from lumi.agent.reactive import ReactiveLoop
from lumi.agent.recall import cost_of
from lumi.agent.reflection_scheduler import ReflectionScheduler
from lumi.agent.session import Session
from lumi.agent.warmup import warm_all, warm_llm
from lumi.agent.windows import WindowAnnouncer
from lumi.artifacts.install import is_model_installed
from lumi.artifacts.models import HARRIER_OSS_V1_270M
from lumi.audio.devices import AudioPlan
from lumi.audio.io import AudioIO
from lumi.character import ExpressionIntent
from lumi.content.pack import CharacterPack, ContentPackError, load_character
from lumi.kernel.arbiter import AttentionArbiter
from lumi.kernel.event import EventBus
from lumi.kernel.hooks import HookRegistry
from lumi.memory.browse import MemoryBrowser
from lumi.memory.retrieval import Retriever
from lumi.memory.store import MemoryStore
from lumi.memory.vectors import MemoryIndex
from lumi.panel.service import PanelService
from lumi.permission.grants import GrantStore
from lumi.permission.kernel import PermissionKernel
from lumi.permission.scope import ScopeLane
from lumi.permission.verifiers import CharacterBindVerifier, CharacterCanonicalizer
from lumi.providers.base import EngineRuntime, ProviderKind
from lumi.providers.embedding.harrier import HarrierEmbeddingProvider
from lumi.providers.llm.base import LLMOptions
from lumi.providers.llm.ollama import OllamaProvider
from lumi.providers.registry import ProviderRegistry
from lumi.providers.stt.faster_whisper import FasterWhisperProvider
from lumi.providers.tts.provider import AivisSpeechProvider
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.state import BootPhase, LlmSetupState
from lumi.storage.audit import AUDIT_SCHEMA, SqliteAuditLog
from lumi.storage.events import EVENTS_SCHEMA, SqliteEventStore
from lumi.storage.memory import EpisodeStore, open_memory
from lumi.storage.retention import RetentionService
from lumi.storage.secret import DpapiSecretStore, get_or_create_db_key
from lumi.storage.sqlite import Database
from lumi.tasks import spawn
from lumi.tools.builtin.character import SetExpressionTool
from lumi.tools.registry import ToolRegistry
from lumi.transport.methods import (
    METHOD_EXPRESSION,
    METHOD_MIC_MUTE,
    METHOD_SETTINGS_UPDATE,
)
from lumi.transport.payload import require_bool, require_str_map
from lumi.transport.protocol import Role
from lumi.transport.server import RequestRefused, WsServer

log = lumi_logging.get_logger(__name__)

#: Settings that reach a running Lumi without a restart. **Everything else needs one**, and
#: the answer says so rather than letting a swapped-in name pretend the model changed
#: (docs/architecture/core.md §6b).
_APPLIED_NOW: Final = frozenset({"locale", "tts_speed", "tts_volume"})

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
        "_audit_db",
        "_embedder",
        "_episodes",
        "_events_db",
        "_index",
        "_indexing",
        "_inspector",
        "_listening",
        "_llm_retry",
        "_loop",
        "_maintenance",
        "_memories",
        "_memory_db",
        "_model",
        "_pack",
        "_panel",
        "_providers",
        "_recorder",
        "_reflecting",
        "_reflection",
        "_retention",
        "_server",
        "_settings",
        "_setup",
        "_task",
        "_warmup",
        "_windows",
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
        self._indexing: asyncio.Task[None] | None = None
        #: The user asked to be remembered and the pass has not run yet. **Held here
        #: rather than on the loop** so that a request survives the checks that find
        #: the conversation still going.
        self._reflecting: asyncio.Task[None] | None = None
        self._warmup: asyncio.Task[None] | None = None
        self._llm_retry: asyncio.Task[None] | None = None
        self._listening = False
        self._audio = AudioIO(plan)
        self._providers = ProviderRegistry()

        key = get_or_create_db_key(DpapiSecretStore(paths.secrets_dir()))
        self._events_db = Database.open(paths.event_db_file(), EVENTS_SCHEMA, key=key)
        self._audit_db = Database.open(paths.audit_db_file(), AUDIT_SCHEMA, key=key)
        self._memory_db = open_memory(paths.memory_db_file(), key=key)
        self._episodes = EpisodeStore(self._memory_db)
        #: **Nothing writes to it until the Reflection Job (2f).** It is built here, and
        #: the sweep below runs from the first release that can hold a memory at all —
        #: the same rule as 2c: do not become able to keep something without the
        #: mechanism that lets go of it.
        self._memories = MemoryStore(self._memory_db)
        self._index = MemoryIndex(self._memory_db)
        #: **`None` when the model was never fetched.** It is 196 MiB acquired at runtime
        #: (ADR-041), and a Lumi that refused to talk without it would be trading the
        #: product for one of its features. Retrieval degrades to keywords and recency.
        self._embedder = self._build_embedder()
        self._retention = RetentionService(
            memory=self._memory_db, events=self._events_db, audit=self._audit_db
        )
        self._maintenance = MaintenanceJobs(
            settings=lambda: self._settings,
            retention=self._retention,
            memories=self._memories,
            index=self._index,
            embedder=self._embedder,
        )

        bus = EventBus(SqliteEventStore(self._events_db))
        tools = self._build_tools(server, bus)

        # **Constructed here, started in `start()`** (creating the idle Activity publishes a
        # DomainEvent, which needs a running loop). Startup sequence step 9
        # → docs/architecture/core.md §7
        self._arbiter = AttentionArbiter(bus)

        #: Held because `_register_providers` needs the voice. **Which speaker Lumi uses is
        #: the Content Pack's decision**, and the Provider has to know it at `load()` time to
        #: initialize that voice rather than a different one
        self._pack = _load_pack()
        #: One per session. **Holds the episode open** for as long as the process runs;
        #: sessions do not end while Lumi is up (Phase 3 gives them boundaries)
        self._recorder = EpisodeRecorder(self._episodes)
        self._windows = WindowAnnouncer(
            server,
            settings=lambda: self._settings,
            audio=self._audio,
            pack=self._pack,
            listening=lambda: self._listening,
        )
        self._loop = self._build_loop(server, tools)
        #: **"When may Lumi go and think" is an agent question** (ADR-045). What to extract
        #: from a transcript stays in `memory/reflection.py`.
        self._reflection = ReflectionScheduler(
            loop=self._loop,
            arbiter=self._arbiter,
            providers=self._providers,
            memories=self._memories,
            episodes=self._episodes,
            maintenance=self._maintenance,
            server=server,
            model=lambda: self._model,
        )

        # **Subscribed, not called from the Arbiter.** The Arbiter does not know the Stage
        # exists, and the send must stay off the barge-in path (`agent/inspector.py`)
        self._inspector = InspectorPublisher(
            self._arbiter, server, lambda: self._loop.last_latency if self._loop else None
        )
        bus.subscribe(self._inspector.on_event)

        # **Registering is what makes a route reachable at all** (ADR-028) — anything
        # unregistered is answered `unknown_method`
        server.on_request(METHOD_SETTINGS_UPDATE, self._update_settings)
        server.on_request(METHOD_MIC_MUTE, self._set_mute)

        # The settings, inspector and memory windows (ADR-042). **Registered whether or
        # not a window is open**: routes belong to Core, not to whoever happens to be
        # connected, and a route that appeared when a window opened would be a race.
        self._panel = PanelService(
            store=self._memories,
            browser=MemoryBrowser(self._memory_db),
            index=self._index,
            episodes=self._episodes,
            retention=self._retention,
            settings_update=self._update_settings,
        )
        self._panel.register(server)

    def _build_tools(self, server: WsServer, bus: EventBus) -> ToolRegistry:
        """Every tool Lumi has, behind the Permission Kernel.

        **There is no second way in** (Invariant 2). `character.set_expression` is L0 and
        effectively passes straight through, but it is registered here and invoked through
        `ToolRegistry.invoke` like anything else — **the path is the same as production's**,
        which is the only way it can be trusted once L3 tools exist.
        """
        tools = ToolRegistry(
            PermissionKernel(GrantStore(), SqliteAuditLog(self._audit_db)),
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
            tts_volume=float(self._settings.tts_volume.value),
            episodes=self._recorder,
            retriever=Retriever(
                self._memories,
                self._index,
                self._embedder,
                # **Costed as the prompt will render it** (`agent.recall`), with the
                # estimator the prompt itself uses. Two approximations would mean packing
                # against one budget and truncating against another.
                cost=lambda record: cost_of(record, estimate_tokens),
            ),
        )

    def _build_embedder(self) -> HarrierEmbeddingProvider | None:
        """The embedding Provider, if its model is on disk.

        **Not loaded here** — `load()` opens an ONNX session, which belongs off the
        constructor. This only decides whether there is one to load.
        """
        if not is_model_installed(HARRIER_OSS_V1_270M, paths.embedding_models_dir()):
            log.info("memory.embedding_absent", model=HARRIER_OSS_V1_270M.name)
            return None
        return HarrierEmbeddingProvider(paths.embedding_models_dir())

    async def _update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        """The Stage asked to change a setting. **Core validates and decides** (ADR-028).

        Model/device changes take effect on the next start. Locale is presentation-only,
        so the settings notification applies it immediately without touching a running turn.
        Speed and volume reach the next turn's `VoiceConfig`, so they need no restart either.
        Swapping a loaded model out is Phase 5's `ModelResourceManager` problem.
        """
        changes = require_str_map(payload, "changes", reason="invalid_payload")

        try:
            self._settings = await asyncio.to_thread(
                settings_module.save, paths.settings_file(), self._settings, changes
            )
        except settings_module.SettingsError as error:
            # **The reason travels back**, never just "failed"
            raise RequestRefused(type(error).__name__) from error

        if self._loop is not None:
            if "tts_speed" in changes:
                self._loop.set_tts_speed(float(self._settings.tts_speed.value))
            if "tts_volume" in changes:
                self._loop.set_tts_volume(float(self._settings.tts_volume.value))

        await self._windows.settings()
        return {
            "applied_at_next_start": any(key not in _APPLIED_NOW for key in changes),
        }

    async def on_panel_connected(self) -> None:
        """A settings, inspector or memory window just opened. **Give it the current state.**

        Core broadcasts when something changes, which is exactly the wrong shape for a
        window that opens later: **every change it cares about has already happened.**
        Without this the settings window waits forever for a snapshot it missed, and the
        inspector stays empty until the next Activity happens to move.

        The memory window is not sent anything here — it asks (`panel.memory.search`),
        because what it should show depends on the page and filter it is on.

        **Failing here must not take the connection down.** A window that opened during
        startup, before the Arbiter is running, is worth a log line and nothing more; the
        next change will reach it.
        """
        try:
            await self._windows.settings()
            await self._inspector.publish()
        except Exception:
            log.warning("panel.snapshot_failed", exc_info=True)

    async def _set_mute(self, payload: dict[str, object]) -> dict[str, object]:
        """Mute or unmute the microphone from the character window.

        **The state is broadcast after the audio stack has actually changed**, never when
        the request arrives. An indicator that goes dark on the press rather than on the
        change is the one thing here that must not be optimistic — it is the only thing
        telling the user whether the room is being listened to.
        """
        wanted = require_bool(payload, "muted", reason="invalid_payload")
        if not self._audio.can_listen:
            raise RequestRefused("no_microphone")
        try:
            await self._audio.set_input_muted(wanted)
        except Exception as error:
            # Unmuting reopens the device, and the device may be gone. **Say so and stay
            # muted**; reporting success would leave the light on over a dead stream.
            log.warning("audio.mute_failed", muted=wanted, error=str(error))
            await self._windows.microphone()
            raise RequestRefused("microphone_unavailable") from error
        await self._windows.microphone()
        return {"muted": self._audio.input_muted}

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
        self._setup.set_ollama_detected_handler(self._schedule_llm_warmup)
        self._setup.set_llm_model_selected_handler(self._select_llm_model)
        # **Before anything writes.** Starting the Arbiter publishes the idle Activity's
        # DomainEvent, and a pass that runs after that has already let this session's first
        # record in before deciding what is too old to keep
        await self._maintenance.expire_old_records()
        await self._maintenance.forget_faded_memories()
        # **Not awaited.** Indexing is the one maintenance job that can take seconds.
        self._indexing = spawn(
            self._maintenance.index_memories(), name="memory.index", event="memory.index_crashed"
        )
        self._reflecting = spawn(
            self._reflection.run(), name="memory.reflect", event="memory.reflect_crashed"
        )
        # **Before anything can arbitrate.** `current()` raises while foreground is unset, so
        # without this the first VAD event kills the reactive loop and Lumi goes deaf for the
        # rest of the session (observed 2026-08-17).
        await self._arbiter.start()
        self._inspector.start()
        # **Sent once at startup.** The Stage shows values, not judgments — including
        # which of them an environment variable is currently overriding
        await self._windows.settings()
        await self._windows.character_model()
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
        self._warmup = spawn(
            warm_all(
                self._providers,
                self._setup,
                self._model,
                on_ready=self._start_listening,
            ),
            name="warmup",
            # **Nobody awaits this task while it runs.** Without a report an unexpected
            # failure stays invisible until GC, and `stop()` is where it would finally
            # surface — as an exception that aborts the rest of shutdown
            event="warmup.crashed",
        )

    def _schedule_llm_warmup(self) -> None:
        """Continues setup after a re-check finds Ollama's local API.

        If the initial all-provider warm-up is still running, the retry waits behind it.
        Repeated UI polls cannot fan out into multiple model loads.
        """
        if self._llm_retry is not None and not self._llm_retry.done():
            return
        self._llm_retry = spawn(
            self._warm_llm_after_detection(), name="llm-recheck", event="llm.recheck_crashed"
        )

    async def _warm_llm_after_detection(self) -> None:
        # Ollama may appear while the initial sequence is still warming STT. Wait for
        # that sequence to finish, then revisit the LLM it already passed. Merely
        # ignoring this signal would leave `detected × starting` stuck forever.
        initial = self._warmup
        if initial is not None and not initial.done():
            try:
                await asyncio.shield(initial)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The initial sequence already reports its own crash. A failure in another
                # provider must not discard the user's successful Ollama installation.
                log.exception("warmup.failed_before_llm_recheck")
        while True:
            await warm_llm(self._providers, self._setup, self._model)
            llm = self._setup.state.llm
            # A missing-model warm-up can stay alive while the consent prompt and pull run.
            # In that case the completion callback cannot schedule a second task because this
            # task is still active. Repeat here so a successful pull is verified immediately.
            if not (
                llm.state is LlmSetupState.DETECTED
                and llm.runtime is EngineRuntime.STARTING
                and llm.reason == "model_checking"
            ):
                break
        if self._setup.boot is BootPhase.READY:
            await self._start_listening()

    async def _select_llm_model(self, model: str) -> None:
        """Applies an explicitly selected setup model before Ollama pulls it."""
        if model == self._model:
            return
        self._settings = await asyncio.to_thread(
            settings_module.save,
            paths.settings_file(),
            self._settings,
            {"llm_model": model},
        )
        self._model = model
        if self._providers.has(ProviderKind.LLM):
            # Registering a replacement does not unload the previous provider. Release its
            # HTTP client and Ollama residency before the new model becomes selected.
            await self._providers.peek(ProviderKind.LLM).unload()
        self._providers.register(OllamaProvider(model))
        if self._loop is not None:
            self._loop.set_llm_model(model)
        await self._windows.settings()

    async def _start_listening(self) -> None:
        """Open voice input only after Core has broadcast `boot: ready` (ADR-033).

        **Never called while the phase is `blocked`.** A screen that says setup is
        incomplete must not be quietly listening behind itself — and with no LLM or no
        STT there is nothing that could answer anyway.
        """
        if self._listening:
            return
        self._listening = True
        try:
            await self._audio.start()
        except asyncio.CancelledError:
            self._listening = False
            raise
        except Exception:
            self._listening = False
            raise

        # **Announced as soon as the stream is open**, before the early return below.
        # A Lumi with no Content Pack still has an open microphone, and the indicator is
        # the only thing on screen saying so.
        await self._windows.microphone()

        if self._loop is None:
            log.warning("conversation.disabled", reason="content pack")
            return
        self._task = spawn(
            self._loop.run(),
            name="reactive",
            event="reactive.crashed",
            on_return="reactive.stopped",
        )
        # **A dead reactive loop means Lumi is deaf, and nothing else notices.** asyncio only
        # surfaces an unretrieved task exception at GC, which is how the missing
        # `arbiter.start()` stayed invisible. **Never let this exit quietly.**
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
        for task in (
            self._warmup,
            self._llm_retry,
            self._indexing,
            self._reflecting,
            self._task,
        ):
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
        self._llm_retry = None
        self._indexing = None
        self._reflecting = None
        self._task = None
        self._listening = False
        await self._inspector.stop()
        if self._loop is not None:
            # **Cancelling the loop task does not stop the turns it spawned.** Each turn
            # runs as its own task, and one waiting on an uncancellable STT would still be
            # alive here — writing an episode into a database that is about to close
            await self._loop.shutdown()
        # **Before the databases close.** A pending episode write against a closed
        # connection loses the last thing the user said
        await self._recorder.close()
        await self._audio.stop()
        try:
            async with asyncio.timeout(30.0):
                await self._providers.unload_all()
        except TimeoutError:
            log.warning("providers.unload_timeout")
        for database in (self._memory_db, self._audit_db, self._events_db):
            database.close()


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
