"""会話に必要なものを組み立てる。**構成だけ。判断は持たない。**

起動シーケンス → docs/architecture/core.md §7

`__main__` から分けているのは、**「何と何が繋がっているか」を1画面で読めるようにする**ため。
Phase 0 の `Greeter` が置き換わったもので、`greeting.py` はここに吸収されて消えた。

## 未セットアップは「壊れている」ではない

Ollama が入っていない / STT のモデルがまだ無い / 入力デバイスが無い —
いずれも**正常な状態**であり、明示して起動を続ける（ADR-023 / docs/architecture/setup.md）。
**黙って劣化させない**が、起動を止めもしない。
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

#: 使う LLM。**モデルの取得は Lumi がしない**（ADR-023）
MODEL_ENV: Final = "LUMI_LLM_MODEL"
DEFAULT_MODEL: Final = "qwen3:4b"
#: STT のモデルサイズ〔Provisional〕。実測してから確定させる
STT_MODEL_ENV: Final = "LUMI_STT_MODEL"
DEFAULT_STT_MODEL: Final = "small"


class ConversationRuntime:
    """会話に要るものを持ち、起動と停止を担う。**使い捨てではない**（プロセスと同じ寿命）。"""

    __slots__ = ("_audio", "_database", "_loop", "_providers", "_setup", "_task")

    def __init__(self, server: WsServer, setup: SetupCoordinator, plan: AudioPlan) -> None:
        self._setup = setup
        self._task: asyncio.Task[None] | None = None
        self._audio = AudioIO(plan)
        self._providers = ProviderRegistry()

        # **イベントの置き場所は決めない。** どこに置くかを決めるには「何を書いてよいか」を
        # 決める必要があり、それは Phase 2 の contracts/privacy.md が決めること。
        # **先に場所だけ決めて、後から意味を付けない。**
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
        """**喋れるかどうかに関わらず起動する。** 足りないものはログと状態に出す。"""
        self._register_providers()
        await self._audio.start()

        if self._loop is None:
            log.warning("conversation.disabled", reason="content pack")
            return
        self._task = asyncio.create_task(self._loop.run(), name="reactive")
        log.info("conversation.started", can_listen=self._audio.can_listen)

    def _register_providers(self) -> None:
        """**未セットアップでも登録する。** 失敗するのは `load()` の時であり、
        そこで初めて「何が足りないか」を具体的に言える。
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
        """**Lumi が起動したものだけ止める**（docs/architecture/core.md §6）。"""
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
    """**人格が無い Lumi は Lumi ではない。** 読めなければ会話を組み立てない。"""
    try:
        return load_character(paths.default_character_dir())
    except ContentPackError as error:
        log.error("content.unavailable", error=str(error))
        return None


def _expression_sender(server: WsServer):  # type: ignore[no-untyped-def]
    """`character.set_expression` の出口。**Tool は WS も Stage も知らない**（注入される）。

    〔Step G〕`stage.character.expression` はまだ `wire.json` に無いので、
    ここではログにだけ出す。**経路は本番と同じ**（マーカー → invoke → ここ）で、
    足りないのは Stage 側の受け口と契約への追加だけ。
    """
    del server

    async def send(intent: ExpressionIntent) -> None:
        log.info("character.expression", **intent.to_payload())

    return send
