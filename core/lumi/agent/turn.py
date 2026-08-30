"""One turn: from an accepted Activity to the last thing Lumi says.

Design → docs/architecture/agent.md §3

```
resume listening → record what was said → resolve TTS/LLM → recall
  → [ LLM stream → speak ] → tool calls → blocks → [ LLM stream → speak ] …
  → await playback
```

The Activity, the proposal and the barge-in belong to the loop that receives VAD events.
**What happens once a turn has been accepted does not**: it is the same sequence whether
the words arrived by microphone or were typed, and it is where the tool loop, the
provenance of a reply and the order of speaking and recording live.

## The order in here is the design

- **Listening resumes first.** Everything after it can take time, and a turn that cannot
  be interrupted until it finishes is a turn with no barge-in
- **Each sentence is spoken as it completes** (`agent/streaming.py`)
- **The reply is recorded before playback is awaited.** Recording afterwards would drop
  the reply whenever a barge-in cancels the wait, leaving the user having heard something
  the history does not contain

## Expression markers go through `invoke` too

Inline markers are the LLM's generated instruction to "change the Stage."
**Never create a path where an LLM-originated effect bypasses the Kernel** (Invariant 2).
`character.set_expression` is L0, so it effectively passes straight through, but **the
path is the same as production.**

## Tool results come back untrusted

A tool result re-enters the prompt as a `ContextBlock` carrying **the provenance the
Registry attached** — never one rebuilt here. Rebuilding it is how a laundering path gets
opened (Invariant 7), and it would look like tidying up.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from lumi import logging as lumi_logging
from lumi.agent.episodes import EpisodeRecorder
from lumi.agent.latency import TurnTimer
from lumi.agent.prompt import ContextBlock, assemble
from lumi.agent.session import Session
from lumi.agent.speech import PlaybackScheduler, StageNotifier
from lumi.agent.streaming import consume_stream
from lumi.agent.voice import VoiceResolver, VoiceScales
from lumi.audio.io import AudioIO
from lumi.audio.playback import SpeakerPlayback
from lumi.character import ExpressionIntent
from lumi.content.pack import CharacterPack
from lumi.kernel.activity import Activity
from lumi.kernel.cancellation import Cancellable, Cancellation
from lumi.providers.base import ProviderError, ProviderKind
from lumi.providers.llm.base import LLMOptions, LLMProvider, ToolCall
from lumi.providers.registry import ProviderRegistry, provider_of
from lumi.providers.tts.base import TTSProvider
from lumi.tools.base import ToolContext, ToolResult
from lumi.tools.registry import ToolRegistry

log = lumi_logging.get_logger(__name__)

#: What a turn may recall, as a `ContextBlock` sequence. **Failure returns nothing**, so
#: the turn still happens — see `ReactiveLoop._recall`.
Recall = Callable[[str, TurnTimer], Awaitable[Sequence[ContextBlock]]]


@dataclass(frozen=True, slots=True)
class LoopLimits:
    """**The limit belongs to the Activity** (docs/architecture/agent.md §3 "Tool loop")."""

    max_steps: int = 4
    #: Deadline for one turn. **Stop once exceeded.** Never keep thinking forever
    turn_timeout_s: float = 60.0


class Turn:
    """Runs one accepted Activity. **Does not decide that it may run** — the Arbiter did."""

    __slots__ = (
        "_audio",
        "_episodes",
        "_limits",
        "_notifier",
        "_options",
        "_pack",
        "_providers",
        "_recall",
        "_session",
        "_tools",
        "_voices",
    )

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        tools: ToolRegistry,
        pack: CharacterPack,
        session: Session,
        notifier: StageNotifier,
        voices: VoiceResolver,
        limits: LoopLimits,
        recall: Recall,
        options: Callable[[], LLMOptions],
        audio: AudioIO | None,
        episodes: EpisodeRecorder | None,
    ) -> None:
        self._providers = providers
        self._tools = tools
        self._pack = pack
        self._session = session
        self._notifier = notifier
        self._voices = voices
        self._limits = limits
        self._recall = recall
        #: **Read per turn, not captured.** Setup can pick a different model while Lumi is
        #: running, and a turn that kept the options it was built with would go on using
        #: the model that was chosen at startup
        self._options = options
        self._audio = audio
        #: Where the conversation is written down. **`None` means it is not** — the typed
        #: test path and anything without a memory database still hold a conversation,
        #: they just leave nothing behind
        self._episodes = episodes

    async def run(
        self, activity: Activity, text: str, timer: TurnTimer, scales: VoiceScales
    ) -> None:
        # **From here on, interruption is allowed.** Reset to a state that can accept the next
        # barge-in
        if self._audio is not None:
            self._audio.resume_listening()

        turn = self._session.record_user_utterance(text)
        if self._episodes is not None:
            # **Not awaited.** The reply is what the user is waiting for; filing the
            # question away is not on that path (`agent/episodes.py`)
            self._episodes.remember_user(
                text, turn.trust_level, correlation_id=str(activity.correlation_id)
            )

        tts: TTSProvider = await provider_of(self._providers, ProviderKind.TTS)
        llm: LLMProvider = await provider_of(self._providers, ProviderKind.LLM)

        scheduler = PlaybackScheduler(
            tts,
            self._require_playback(),
            self._notifier,
            voice=self._voices.resolve(tts, scales),
            cancel_token=activity.cancel_token,
            timer=timer,
        )
        # **Playback is `hard`.** Muting the buffer silences it instantly
        speech = Cancellable(
            id=f"speech:{activity.id}",
            label="TTS playback",
            contract=Cancellation.HARD,
            kill=scheduler.abort,
        )
        activity.cancellables.append(speech)

        blocks: list[ContextBlock] = list(await self._recall(text, timer))
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

        async def express(intent: ExpressionIntent) -> None:
            await self._apply_expression(activity, intent)

        spoken = await consume_stream(
            llm.stream(
                prompt.messages,
                self._tools.list_exposed(),
                self._options(),
                activity.cancel_token,
            ),
            scheduler=scheduler,
            timer=timer,
            cancel_token=activity.cancel_token,
            express=express,
        )

        # **Inherits the join of the inputs** (not "always tainted because it's LLM output")
        self._session.record_lumi_turn(spoken.reply, prompt.context.effective_trust)
        if self._episodes is not None:
            self._episodes.remember_lumi(
                spoken.reply,
                prompt.context.effective_trust,
                correlation_id=str(activity.correlation_id),
            )
        return spoken.calls

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

    def _require_playback(self) -> SpeakerPlayback:
        """If there's no output, **fail explicitly.** Never silently converse in silence."""
        if self._audio is None or self._audio.playback is None:
            raise ProviderError("no_playback", "No audio playback target available")
        return self._audio.playback


def _as_block(source: str, result: ToolResult) -> ContextBlock:
    """Converts a `ToolResult` into a ContextBlock. **Uses the provenance the Registry
    attached, unchanged.**

    Reconstructing it here would open a hole in Invariant 7.
    """
    content = str(result.value) if result.ok else f"Failed: {result.error}"
    return ContextBlock(
        source=source,
        content=content,
        provenance_class=result.provenance_class,
        trust_level=result.trust_level,
    )
