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
import os
from pathlib import Path
from typing import Final

from lumi import logging as lumi_logging
from lumi import paths
from lumi.agent.reactive import ReactiveLoop
from lumi.agent.session import Session
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
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)

#: The LLM to use. **Lumi doesn't fetch the model itself** (ADR-023)
MODEL_ENV: Final = "LUMI_LLM_MODEL"
DEFAULT_MODEL: Final = "qwen3:4b"
#: STT model size [Provisional]. To be finalized after measurement
STT_MODEL_ENV: Final = "LUMI_STT_MODEL"
DEFAULT_STT_MODEL: Final = "small"


class ConversationRuntime:
    """Holds what conversation needs and owns start/stop. **Not disposable** (lives as long as the
    process).
    """

    __slots__ = ("_audio", "_database", "_loop", "_providers", "_setup", "_task")

    def __init__(self, server: WsServer, setup: SetupCoordinator, plan: AudioPlan) -> None:
        self._setup = setup
        self._task: asyncio.Task[None] | None = None
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

        pack = _load_pack()
        self._loop = (
            None
            if pack is None
            else ReactiveLoop(
                arbiter=AttentionArbiter(bus),
                providers=self._providers,
                tools=tools,
                pack=pack,
                notifier=server,
                options=LLMOptions(model=os.environ.get(MODEL_ENV, DEFAULT_MODEL)),
                session=Session(),
                audio=self._audio,
            )
        )

    async def start(self) -> None:
        """**Starts regardless of whether it can speak.** Missing pieces show up in logs/state."""
        self._register_providers()
        await self._audio.start()

        if self._loop is None:
            log.warning("conversation.disabled", reason="content pack")
            return
        self._task = asyncio.create_task(self._loop.run(), name="reactive")
        log.info("conversation.started", can_listen=self._audio.can_listen)

    def _register_providers(self) -> None:
        """**Registered even when not set up.** Failure happens at `load()` time, which
        is the first point where "what's missing" can be stated concretely.
        """
        state = self._setup.state
        if state.usable and state.port is not None:
            executable = Path(state.executable) if state.executable else None
            self._providers.register(AivisSpeechProvider(state.port, executable=executable))
        else:
            log.info("tts.not_registered", state=str(state.state))

        self._providers.register(OllamaProvider(os.environ.get(MODEL_ENV, DEFAULT_MODEL)))
        self._providers.register(
            FasterWhisperProvider(
                os.environ.get(STT_MODEL_ENV, DEFAULT_STT_MODEL),
                paths.models_dir() / "whisper",
            )
        )

    async def stop(self) -> None:
        """**Only stops what Lumi itself started** (docs/architecture/core.md §6)."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._audio.stop()
        try:
            async with asyncio.timeout(30.0):
                await self._providers.unload_all()
        except TimeoutError:
            log.warning("providers.unload_timeout")
        self._database.close()


def _load_pack() -> CharacterPack | None:
    """**Lumi without a persona isn't Lumi.** If it can't be read, conversation isn't assembled."""
    try:
        return load_character(paths.default_character_dir())
    except ContentPackError as error:
        log.error("content.unavailable", error=str(error))
        return None


def _expression_sender(server: WsServer):  # type: ignore[no-untyped-def]
    """Exit point for `character.set_expression`. **The Tool knows nothing about WS or
    the Stage** (it's injected).

    [Step G] `stage.character.expression` isn't in `wire.json` yet, so this only logs
    for now. **The path is the same as production** (marker → invoke → here); all
    that's missing is the Stage-side receiver and the contract addition.
    """
    del server

    async def send(intent: ExpressionIntent) -> None:
        log.info("character.expression", **intent.to_payload())

    return send
