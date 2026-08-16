"""Reactive Loop — 話しかけられたら答える。

設計 → docs/architecture/agent.md §3

```
speech-end → STT → Activity 提案 → 記憶検索（Phase 1 は 0件）→ PromptAssembly
  → LLM stream ─┬→ text  → マーカー除去 → 文分割 → TTS → 再生 → リップシンク
                ├→ <|ACT|> → ToolRegistry.invoke（表情）
                └→ tool call → Kernel 実行契約 → ContextBlock（untrusted）→ 再投入
```

## barge-in はここでは起こさない

**「音が止まる」は VAD スレッドが同期的に行う**（`mute_flag`）。
**「Activity が止まる」は `arbiter.interrupt()`** であり、その入口は `on_speech_started()`。
このループは、止められる側として `cancel_token` と `Cancellable` を正しく提供する責任だけを負う。

## 表情マーカーも `invoke` を通る

インラインマーカーは LLM が生成した「Stage を変えろ」という指示である。
**LLM 由来の作用が Kernel を経由しない経路を作らない**（Invariant 2）。
`character.set_expression` は L0 なので実質素通りするが、**経路は本番と同じ**。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from lumi import logging as lumi_logging
from lumi.agent.markers import MarkerStream
from lumi.agent.prompt import ContextBlock, assemble
from lumi.agent.sentences import SentenceStream
from lumi.agent.session import Session
from lumi.agent.speech import PlaybackScheduler, StageNotifier
from lumi.audio.io import AudioIO
from lumi.audio.playback import SpeakerPlayback
from lumi.audio.vad import VadEvent
from lumi.character import ExpressionIntent
from lumi.content.pack import CharacterPack
from lumi.kernel.activity import Activity, ActivityKind, ActivityProposal, Actor
from lumi.kernel.arbiter import Accepted, AttentionArbiter
from lumi.kernel.cancellation import Cancellable, Cancellation, CancelToken
from lumi.kernel.ids import new_correlation_id
from lumi.providers.base import ProviderError, ProviderKind
from lumi.providers.llm.base import (
    Finish,
    LLMFailure,
    LLMOptions,
    LLMProvider,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)
from lumi.providers.registry import ProviderRegistry
from lumi.providers.stt.base import AudioBuffer, STTProvider
from lumi.providers.tts.base import TTSProvider, VoiceConfig
from lumi.tools.base import ToolContext, ToolResult
from lumi.tools.registry import ToolRegistry

log = lumi_logging.get_logger(__name__)

#: STT に渡す言語。Phase 1 は日本語固定〔Provisional〕
LANGUAGE: Final = "ja"


@dataclass(frozen=True, slots=True)
class LoopLimits:
    """**上限は Activity が持つ**（docs/architecture/agent.md §3「ツールループ」）。"""

    max_steps: int = 4
    #: 1ターンの締切。**超えたら止める。** 無限に考え続けない
    turn_timeout_s: float = 60.0


class ReactiveLoop:
    """会話1本を回す。**Arbiter の下にいる**（自分で foreground を取らない）。"""

    __slots__ = (
        "_arbiter",
        "_audio",
        "_limits",
        "_notifier",
        "_options",
        "_pack",
        "_providers",
        "_session",
        "_tools",
    )

    def __init__(
        self,
        *,
        arbiter: AttentionArbiter,
        providers: ProviderRegistry,
        tools: ToolRegistry,
        pack: CharacterPack,
        notifier: StageNotifier,
        options: LLMOptions,
        session: Session | None = None,
        limits: LoopLimits | None = None,
        audio: AudioIO | None = None,
    ) -> None:
        self._arbiter = arbiter
        self._providers = providers
        self._tools = tools
        self._pack = pack
        self._notifier = notifier
        self._options = options
        self._session = session or Session()
        self._limits = limits or LoopLimits()
        self._audio = audio

    @property
    def session(self) -> Session:
        return self._session

    # ── 入口 ──────────────────────────────────────────────

    async def run(self) -> None:
        """VAD イベントを引き取る。**asyncio 側の入口。**

        **1ターンは別タスクで走らせる。** ここで `await` すると、
        喋っている最中に来る `SPEECH_STARTED`（＝ barge-in）を引き取れなくなる。
        遮られたことに気づけないループは、barge-in を持っていないのと同じである。
        """
        if self._audio is None or not self._audio.can_listen:
            log.warning("reactive.no_input")
            return

        turns: set[asyncio.Task[None]] = set()
        async for event, audio in self._audio.events():
            if event is VadEvent.SPEECH_STARTED:
                await self.on_speech_started()
            elif event is VadEvent.SPEECH_ENDED and audio is not None:
                task = asyncio.create_task(self.on_speech_ended(audio), name="turn")
                turns.add(task)
                task.add_done_callback(turns.discard)

    async def on_speech_started(self) -> None:
        """**barge-in。** 音はもう止まっている（VAD スレッド）。Activity を止める。"""
        result = await self._arbiter.interrupt("user_speech")
        log.info(
            "reactive.interrupted",
            activity=str(result.activity_id),
            stopped=result.stopped,
            abandoned=result.abandoned,
        )

    async def on_speech_ended(self, audio: AudioBuffer) -> None:
        """発話区間が確定した。**STT にかけてから Activity を提案する。**

        提案の前に STT をするのは、**何も言っていなかった場合に Activity を作らないため**。
        空の会話 Activity は idle を無駄に退避させ、Inspector を汚す。
        """
        try:
            stt: STTProvider = await self._get(ProviderKind.STT)
            transcription = await stt.transcribe(audio, LANGUAGE, CancelToken())
        except ProviderError as error:
            # **黙って聞き流さない。** 何が足りないかはセットアップ状態が持っている
            log.warning("reactive.stt_failed", error=str(error))
            return

        text = transcription.text.strip()
        if not text:
            log.info("reactive.empty_transcription")
            return
        await self.handle_text(text)

    async def handle_text(self, text: str) -> None:
        """テキスト入力からの1ターン。**音声経路と同じ道を通る。**"""
        proposal = ActivityProposal(
            kind=ActivityKind.CONVERSATION,
            actor=Actor.USER_INITIATED,
            intent="reply",
            correlation_id=new_correlation_id(),
            # **ユーザー発話は保留しない。** 後で蒸し返されても困る
            deferrable=False,
            deadline=datetime.now(UTC) + timedelta(seconds=self._limits.turn_timeout_s),
        )
        outcome = await self._arbiter.propose(proposal)
        if not isinstance(outcome, Accepted):
            log.warning("reactive.not_accepted", outcome=type(outcome).__name__)
            return

        activity = outcome.activity
        failed = False
        try:
            await self._converse(activity, text)
        except ProviderError as error:
            # **喋れなかったことを Activity の状態に残す**（黙って成功にしない）
            log.warning("reactive.turn_failed", error=str(error))
            failed = True
        finally:
            if self._arbiter.current().id == activity.id:
                # 中断されていれば Arbiter が既に片付けている。二重遷移させない
                await self._arbiter.complete(activity.id, failed=failed)

    # ── 1ターン ───────────────────────────────────────────

    async def _converse(self, activity: Activity, text: str) -> None:
        started = time.perf_counter()
        # **ここから先は遮られてよい。** 次の barge-in を受けられる状態に戻す
        if self._audio is not None:
            self._audio.resume_listening()

        self._session.record_user_utterance(text)

        tts: TTSProvider = await self._get(ProviderKind.TTS)
        llm: LLMProvider = await self._get(ProviderKind.LLM)

        scheduler = PlaybackScheduler(
            tts,
            self._require_playback(),
            self._notifier,
            voice=self._voice(tts),
            cancel_token=activity.cancel_token,
        )
        # **再生は `hard`。** バッファのミュートで即座に無音にできる
        speech = Cancellable(
            id=f"speech:{activity.id}",
            label="TTS 再生",
            contract=Cancellation.HARD,
            kill=scheduler.abort,
        )
        activity.cancellables.append(speech)

        blocks: list[ContextBlock] = []
        try:
            for step in range(self._limits.max_steps):
                if activity.cancel_token.is_set:
                    break
                calls = await self._one_step(activity, llm, scheduler, blocks)
                if not calls:
                    break
                blocks += [await self._run_tool(activity, call) for call in calls]
                log.info("reactive.tool_step", step=step + 1, calls=len(calls))
            await scheduler.finish()
        finally:
            speech.mark_finished()

        log.info("reactive.turn_done", elapsed_ms=round((time.perf_counter() - started) * 1000, 1))

    async def _one_step(
        self,
        activity: Activity,
        llm: LLMProvider,
        scheduler: PlaybackScheduler,
        blocks: list[ContextBlock],
    ) -> list[ToolCall]:
        """1回の LLM ストリーム。**喋りながら**ツール呼び出しを集める。"""
        prompt = assemble(persona=self._pack.persona, session=self._session, blocks=blocks)

        markers = MarkerStream()
        sentences = SentenceStream()
        spoken: list[str] = []
        calls: list[ToolCall] = []

        async for event in llm.stream(
            prompt.messages,
            self._tools.list_exposed(),
            self._options,
            activity.cancel_token,
        ):
            if activity.cancel_token.is_set:
                # `cooperative`。**次のチェックポイントで止まる**
                break
            match event:
                case TextDelta(text=text):
                    chunk = markers.feed(text)
                    spoken.append(chunk.text)
                    for intent in chunk.intents:
                        await self._apply_expression(activity, intent)
                    for sentence in sentences.feed(chunk.text):
                        scheduler.speak(sentence)
                case ReasoningDelta():
                    # **思考は音声化しない。** 吹き出しにも出さない（Inspector だけ）
                    pass
                case ToolCall():
                    calls.append(event)
                case Finish():
                    pass
                case LLMFailure(message=message):
                    # 途中で壊れた。**もう喋ってしまった分と整合を取る必要がある**ので、
                    # 例外にせずここで止める
                    log.warning("reactive.llm_failed", error=message)

        tail = markers.flush()
        spoken.append(tail)
        for sentence in [*sentences.feed(tail), *sentences.flush()]:
            scheduler.speak(sentence)

        # **入力の join を継承する**（「LLM 出力だから常に tainted」ではない）
        self._session.record_lumi_turn("".join(spoken), prompt.context.effective_trust)
        return calls

    # ── ツール ────────────────────────────────────────────

    async def _run_tool(self, activity: Activity, call: ToolCall) -> ContextBlock:
        result = await self._tools.invoke(call.name, self._tool_context(activity), call.arguments)
        # **ツール結果は untrusted。** session_trust は sticky に汚染される
        self._session.observe(result.trust_level)
        return _as_block(f"tool:{call.name}", result)

    async def _apply_expression(self, activity: Activity, intent: ExpressionIntent) -> None:
        """マーカーも `invoke` を通す。**LLM 由来の作用にバイパスを作らない。**"""
        result = await self._tools.invoke(
            "character.set_expression",
            self._tool_context(activity),
            {"emotion": intent.emotion.value, "intensity": intent.intensity},
        )
        if not result.ok:
            log.info("reactive.expression_refused", error=result.error)

    def _tool_context(self, activity: Activity) -> ToolContext:
        return ToolContext(
            cancel_token=activity.cancel_token,
            actor=activity.actor,
            activity_id=activity.id,
            correlation_id=activity.correlation_id,
            #: **Policy が見るのは実効 trust。** 3スコープの join を渡す
            input_trust_level=self._session.context().effective_trust,
            deadline=activity.deadline,
        )

    # ── Provider ──────────────────────────────────────────

    async def _get[T](self, kind: ProviderKind) -> T:
        """`ProviderRegistry` は kind ごとに1つを返す。**未セットアップなら例外。**"""
        return cast("T", await self._providers.get(kind))

    def _voice(self, tts: TTSProvider) -> VoiceConfig:
        """Content Pack が話者を指定していなければ、**エンジンの既定に委ねる。**

        既定を Core が決め打たないのは、**入っているモデルが環境によって違う**ため
        （AivisSpeech は実行時にモデルを取得する）。
        """
        speaker = self._pack.voice.speaker
        if speaker is not None:
            return VoiceConfig(speaker=speaker, name=self._pack.voice.credit.name)
        default = getattr(tts, "default_voice", None)
        if default is None:
            raise ProviderError("no_voice", "話者を決められない")
        return cast("VoiceConfig", default())

    def _require_playback(self) -> SpeakerPlayback:
        """出力が無いなら**明示的に失敗する。** 黙って無音で会話しない。"""
        if self._audio is None or self._audio.playback is None:
            raise ProviderError("no_playback", "音声の出力先が無い")
        return self._audio.playback


def _as_block(source: str, result: ToolResult) -> ContextBlock:
    """`ToolResult` を ContextBlock に。**provenance は Registry が付けたものをそのまま使う。**

    ここで作り直せてしまうと Invariant 7 の穴になる。
    """
    content = str(result.value) if result.ok else f"失敗: {result.error}"
    return ContextBlock(
        source=source,
        content=content,
        provenance_class=result.provenance_class,
        trust_level=result.trust_level,
    )
