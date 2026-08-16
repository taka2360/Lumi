"""Reactive Loop — responds when spoken to.

Design → docs/architecture/agent.md §3

```
speech-end → STT → Activity proposal → memory search (0 results in Phase 1) → PromptAssembly
  → LLM stream ─┬→ text  → strip markers → sentence split → TTS → playback → lip sync
                ├→ <|ACT|> → ToolRegistry.invoke (expression)
                └→ tool call → Kernel execution contract → ContextBlock (untrusted) → re-fed
```

## barge-in does not happen here

**"Sound stopping" is done synchronously by the VAD thread** (`mute_flag`).
**"Activity stopping" is `arbiter.interrupt()`**, entered through `on_speech_started()`.
This loop's only responsibility is to correctly offer `cancel_token` and `Cancellable` as
the thing that gets stopped.

## Expression markers go through `invoke` too

Inline markers are the LLM's generated instruction to "change the Stage."
**Never create a path where an LLM-originated effect bypasses the Kernel** (Invariant 2).
`character.set_expression` is L0, so it effectively passes straight through, but **the
path is the same as production.**
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, cast

from lumi import logging as lumi_logging
from lumi.agent.latency import TurnLatency, TurnTimer
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

#: Language passed to STT. Fixed to Japanese in Phase 1 [Provisional]
LANGUAGE: Final = "ja"


@dataclass(frozen=True, slots=True)
class LoopLimits:
    """**The limit belongs to the Activity** (docs/architecture/agent.md §3 "Tool loop")."""

    max_steps: int = 4
    #: Deadline for one turn. **Stop once exceeded.** Never keep thinking forever
    turn_timeout_s: float = 60.0


class ReactiveLoop:
    """Runs one conversation. **Lives under the Arbiter** (never takes foreground itself)."""

    __slots__ = (
        "_arbiter",
        "_audio",
        "_last_latency",
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
        self._last_latency: TurnLatency | None = None

    @property
    def session(self) -> Session:
        return self._session

    @property
    def last_latency(self) -> TurnLatency | None:
        """The most recent turn's breakdown. **What the Inspector shows** (roadmap Phase 1)."""
        return self._last_latency

    # ── Entry point ──────────────────────────────────────────────

    async def run(self) -> None:
        """Consumes VAD events. **The entry point on the asyncio side.**

        **Each turn runs as a separate task.** `await`ing here would make it impossible
        to pick up a `SPEECH_STARTED` (i.e. a barge-in) arriving while Lumi is speaking.
        A loop that can't notice it was interrupted is the same as having no barge-in at all.
        """
        if self._audio is None or not self._audio.can_listen:
            log.warning("reactive.no_input")
            return

        turns: set[asyncio.Task[None]] = set()
        async for event, audio, audio_at in self._audio.events():
            if event is VadEvent.SPEECH_STARTED:
                await self.on_speech_started()
            elif event is VadEvent.SPEECH_ENDED and audio is not None:
                task = asyncio.create_task(self.on_speech_ended(audio, audio_at), name="turn")
                turns.add(task)
                task.add_done_callback(turns.discard)

    async def on_speech_started(self) -> None:
        """**Barge-in.** The sound has already stopped (VAD thread). Stop the Activity."""
        result = await self._arbiter.interrupt("user_speech")
        log.info(
            "reactive.interrupted",
            activity=str(result.activity_id),
            stopped=result.stopped,
            abandoned=result.abandoned,
        )

    async def on_speech_ended(self, audio: AudioBuffer, ended_at: float | None = None) -> None:
        """A speech segment was confirmed. **Run STT before proposing an Activity.**

        STT happens before the proposal so that **no Activity gets created if nothing was
        actually said**. An empty conversation Activity needlessly preempts idle and
        clutters the Inspector.

        `ended_at` is when the user actually stopped talking, not when this was called.
        **The gap between the two is `vad_ms`** and it is the largest fixed cost in the
        budget (docs/architecture/audio.md §7), so it must not be measured from here.
        """
        timer = TurnTimer(new_correlation_id(), started_at=ended_at)
        timer.since_start("vad_ms")
        try:
            stt: STTProvider = await self._get(ProviderKind.STT)
            with timer.span("stt_ms"):
                transcription = await stt.transcribe(audio, LANGUAGE, CancelToken())
        except ProviderError as error:
            # **Don't silently ignore it.** What's missing is tracked by setup state
            log.warning("reactive.stt_failed", error=str(error))
            return

        text = transcription.text.strip()
        if not text:
            log.info("reactive.empty_transcription")
            return
        await self.handle_text(text, timer=timer)

    async def handle_text(self, text: str, *, timer: TurnTimer | None = None) -> None:
        """One turn from text input. **Takes the same path as the voice route.**

        Without a `timer`, one starts here — so `vad_ms` and `stt_ms` are simply absent
        rather than zero. **Typed input never went through those stages**, and recording
        them as 0 would drag the percentiles toward a speed the voice path never reaches.
        """
        timer = timer or TurnTimer(new_correlation_id())
        # Phase 1 has no memory retrieval. **Recorded explicitly so the spans stay contiguous**
        timer.record("retrieve_ms", 0)
        proposal = ActivityProposal(
            kind=ActivityKind.CONVERSATION,
            actor=Actor.USER_INITIATED,
            intent="reply",
            correlation_id=new_correlation_id(),
            # **A user utterance is never deferred.** Having it dredged up later would be
            # problematic
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
            await self._converse(activity, text, timer)
        except ProviderError as error:
            # **Record in the Activity's state that it failed to speak** (never silently mark it a
            # success)
            log.warning("reactive.turn_failed", error=str(error))
            failed = True
        finally:
            # **An interrupted turn reports too.** Barge-in is the normal case, and how far
            # the turn got before being cut off is exactly what needs measuring
            self._last_latency = timer.emit()
            if self._arbiter.current().id == activity.id:
                # If it was interrupted, the Arbiter has already cleaned up. Don't double-transition
                await self._arbiter.complete(activity.id, failed=failed)

    # ── One turn ───────────────────────────────────────────

    async def _converse(self, activity: Activity, text: str, timer: TurnTimer) -> None:
        # **From here on, interruption is allowed.** Reset to a state that can accept the next
        # barge-in
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
            timer=timer,
        )
        # **Playback is `hard`.** Muting the buffer silences it instantly
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
                calls = await self._one_step(activity, llm, scheduler, blocks, timer)
                if not calls:
                    break
                blocks += [await self._run_tool(activity, call) for call in calls]
                log.info("reactive.tool_step", step=step + 1, calls=len(calls))
            await scheduler.finish()
        finally:
            speech.mark_finished()

    async def _one_step(
        self,
        activity: Activity,
        llm: LLMProvider,
        scheduler: PlaybackScheduler,
        blocks: list[ContextBlock],
        timer: TurnTimer,
    ) -> list[ToolCall]:
        """One LLM stream. **Collects tool calls while speaking.**

        The timer marks are `mark_once`: the tool loop assembles a prompt every step, but the
        spans mean **the first** of each — what the user is actually waiting on.

        **What this does not measure well**: if step 1 is a pure tool call with no text, the
        tool round-trip lands inside `llm_first_token_ms`. Acceptable while Phase 1 has one
        L0 tool; revisit when tools become common.
        """
        with timer.span("assemble_ms"):
            prompt = assemble(persona=self._pack.persona, session=self._session, blocks=blocks)

        markers = MarkerStream()
        sentences = SentenceStream()
        spoken: list[str] = []
        calls: list[ToolCall] = []

        timer.begin("llm_first_token_ms")
        async for event in llm.stream(
            prompt.messages,
            self._tools.list_exposed(),
            self._options,
            activity.cancel_token,
        ):
            if activity.cancel_token.is_set:
                # `cooperative`. **Stops at the next checkpoint**
                break
            match event:
                case TextDelta(text=text):
                    if timer.end("llm_first_token_ms") is not None:
                        # The wait for a full TTS-able unit starts here (audio.md §7)
                        timer.begin("llm_first_segment_ms")
                    chunk = markers.feed(text)
                    spoken.append(chunk.text)
                    for intent in chunk.intents:
                        await self._apply_expression(activity, intent)
                    for sentence in sentences.feed(chunk.text):
                        timer.end("llm_first_segment_ms")
                        scheduler.speak(sentence)
                case ReasoningDelta():
                    # **Reasoning is never spoken.** Not shown in the speech bubble either
                    # (Inspector only)
                    pass
                case ToolCall():
                    calls.append(event)
                case Finish():
                    pass
                case LLMFailure(message=message):
                    # Broke mid-stream. **What's already been spoken needs to stay
                    # consistent**, so this stops here instead of raising an exception
                    log.warning("reactive.llm_failed", error=message)

        tail = markers.flush()
        spoken.append(tail)
        for sentence in [*sentences.feed(tail), *sentences.flush()]:
            timer.end("llm_first_segment_ms")
            scheduler.speak(sentence)

        # **Inherits the join of the inputs** (not "always tainted because it's LLM output")
        self._session.record_lumi_turn("".join(spoken), prompt.context.effective_trust)
        return calls

    # ── Tools ────────────────────────────────────────────

    async def _run_tool(self, activity: Activity, call: ToolCall) -> ContextBlock:
        result = await self._tools.invoke(call.name, self._tool_context(activity), call.arguments)
        # **Tool results are untrusted.** session_trust gets stickily tainted
        self._session.observe(result.trust_level)
        return _as_block(f"tool:{call.name}", result)

    async def _apply_expression(self, activity: Activity, intent: ExpressionIntent) -> None:
        """Markers also go through `invoke`. **No bypass for LLM-originated effects.**"""
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
            #: **What Policy looks at is effective trust.** Pass the join of the 3 scopes
            input_trust_level=self._session.context().effective_trust,
            deadline=activity.deadline,
        )

    # ── Provider ──────────────────────────────────────────

    async def _get[T](self, kind: ProviderKind) -> T:
        """`ProviderRegistry` returns one per kind. **Raises if not set up.**"""
        return cast("T", await self._providers.get(kind))

    def _voice(self, tts: TTSProvider) -> VoiceConfig:
        """If the Content Pack doesn't specify a speaker, **defer to the engine's default.**

        Core doesn't hardcode a default because **which models are installed varies by
        environment** (AivisSpeech fetches models at runtime).
        """
        speaker = self._pack.voice.speaker
        if speaker is not None:
            return VoiceConfig(speaker=speaker, name=self._pack.voice.credit.name)
        default = getattr(tts, "default_voice", None)
        if default is None:
            raise ProviderError("no_voice", "話者を決められない")
        return cast("VoiceConfig", default())

    def _require_playback(self) -> SpeakerPlayback:
        """If there's no output, **fail explicitly.** Never silently converse in silence."""
        if self._audio is None or self._audio.playback is None:
            raise ProviderError("no_playback", "音声の出力先が無い")
        return self._audio.playback


def _as_block(source: str, result: ToolResult) -> ContextBlock:
    """Converts a `ToolResult` into a ContextBlock. **Uses the provenance the Registry
    attached, unchanged.**

    Reconstructing it here would open a hole in Invariant 7.
    """
    content = str(result.value) if result.ok else f"失敗: {result.error}"
    return ContextBlock(
        source=source,
        content=content,
        provenance_class=result.provenance_class,
        trust_level=result.trust_level,
    )
