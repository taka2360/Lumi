"""Reactive Loop — responds when spoken to.

Design → docs/architecture/agent.md §3

```
speech-end → STT → Activity proposal → [ agent/turn.py ]
```

**This is the half that decides whether there is a turn at all**: VAD events in, an
utterance transcribed, an Activity proposed, and — once it comes back accepted — the
turn itself handed to `agent/turn.py`. The reply, the tool loop and the speaking are
there, because they are the same whether the words were spoken or typed.

## barge-in does not happen here

**"Sound stopping" is done synchronously by the VAD thread** (`mute_flag`).
**"Activity stopping" is `arbiter.interrupt()`**, entered through `on_speech_started()`.
This loop's only responsibility is to correctly offer `cancel_token` and `Cancellable` as
the thing that gets stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

import numpy as np

from lumi import logging as lumi_logging
from lumi.agent.episodes import EpisodeRecorder
from lumi.agent.latency import TurnLatency, TurnTimer, record_stt
from lumi.agent.prompt import ContextBlock
from lumi.agent.recall import BLOCK_OVERHEAD_TOKENS, MAX_MEMORY_BLOCKS, to_blocks
from lumi.agent.session import Session
from lumi.agent.speech import StageNotifier
from lumi.agent.stt import SpeculativeStt
from lumi.agent.turn import LoopLimits, Turn
from lumi.agent.voice import VoiceResolver, VoiceScales, validate_speed, validate_volume
from lumi.audio.io import AudioIO
from lumi.audio.vad import SAMPLE_RATE, VadEvent
from lumi.content.pack import CharacterPack
from lumi.kernel.activity import ActivityKind, ActivityProposal, Actor
from lumi.kernel.arbiter import Accepted, AttentionArbiter
from lumi.kernel.cancellation import CancelToken
from lumi.kernel.ids import new_correlation_id
from lumi.memory.phrases import asked_to_remember
from lumi.memory.retrieval import Retriever
from lumi.providers.base import ProviderError, ProviderKind
from lumi.providers.llm.base import LLMOptions
from lumi.providers.registry import ProviderRegistry, provider_of
from lumi.providers.stt.base import AudioBuffer, STTProvider, Transcription
from lumi.tasks import spawn
from lumi.tools.registry import ToolRegistry
from lumi.transport.methods import METHOD_USER_SAID
from lumi.transport.protocol import Role

log = lumi_logging.get_logger(__name__)

#: What memories may spend of the prompt [Provisional]. **A fraction of the whole budget**
#: (`prompt.PROMPT_BUDGET_TOKENS`): remembering must not crowd out the conversation that is
#: happening now, and what does not fit comes back as `dropped` rather than disappearing.
MEMORY_BUDGET_TOKENS: Final = 400

#: Language passed to STT. Fixed to Japanese in Phase 1 [Provisional]
LANGUAGE: Final = "ja"


class ReactiveLoop:
    """Runs one conversation. **Lives under the Arbiter** (never takes foreground itself)."""

    __slots__ = (
        "_arbiter",
        "_asked_to_remember",
        "_audio",
        "_clock",
        "_last_latency",
        "_last_turn_at",
        "_limits",
        "_notifier",
        "_options",
        "_providers",
        "_retriever",
        "_session",
        "_stt",
        "_tts_speed",
        "_tts_volume",
        "_turn",
        "_turns",
        "_writes",
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
        tts_speed: float = 1.2,
        tts_volume: float = 1.0,
        episodes: EpisodeRecorder | None = None,
        retriever: Retriever | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._arbiter = arbiter
        self._providers = providers
        self._notifier = notifier
        self._options = options
        self._session = session or Session()
        self._limits = limits or LoopLimits()
        self._audio = audio
        #: Memory search. **`None` means the turn runs without remembering anything** —
        #: the typed test path, and any session whose embedding model was never fetched
        self._retriever = retriever
        #: Wall clock, for the things that are dated rather than timed. `TurnTimer.now()`
        #: is monotonic — right for spans, **meaningless as a date**, and decay and recency
        #: are both functions of a date.
        self._clock = clock
        self._tts_speed = validate_speed(tts_speed)
        #: **Scales the Content Pack's volume**, so `1.0` is whatever the pack asked for
        self._tts_volume = validate_volume(tts_volume)
        self._last_latency: TurnLatency | None = None
        #: When the last turn finished. **Starts at construction**, so a Lumi nobody has
        #: spoken to yet counts as idle — which is what makes the first idle pass pick up
        #: whatever the previous session left unreflected.
        self._last_turn_at = self._clock()
        #: The user said something like 「覚えておいて」. **Reflection reads and clears
        #: it** — the phrase is a request to write something down, and waiting out the
        #: full idle period would answer it minutes after it stopped meaning anything.
        self._asked_to_remember = False
        #: The turns currently running. **Held on the loop, not inside `run`**: shutdown
        #: has to be able to reach them, and a turn that outlives the databases writes an
        #: episode into a closed connection
        self._turns: set[asyncio.Task[None]] = set()
        #: Memory bookkeeping started by a turn and outliving it. **Not turns**: a set
        #: named "the turns currently running" is the obvious thing to consult for "is
        #: Lumi mid-conversation", and counting a recall tally as one would make Lumi
        #: look busy to whoever asks next
        self._writes: set[asyncio.Task[None]] = set()
        #: **The only place STT is started** (ADR-039). Speculations begin while VAD is
        #: still waiting out the silence, and `SPEECH_ENDED` adopts one instead of starting
        #: its own — two entry points would mean two inferences in flight
        self._stt = SpeculativeStt(self._transcribe)
        #: What happens once a turn is accepted. **Built once and reused**: it holds no
        #: state of its own, and the two things that do change — the model and the
        #: sliders — reach it as a callable and as an argument rather than as a copy
        self._turn = Turn(
            providers=providers,
            tools=tools,
            pack=pack,
            session=self._session,
            notifier=notifier,
            voices=VoiceResolver(pack),
            limits=self._limits,
            recall=self._recall,
            options=lambda: self._options,
            audio=audio,
            episodes=episodes,
        )

    @property
    def session(self) -> Session:
        return self._session

    def idle_for(self) -> timedelta:
        """How long since the last turn ended. **What decides that reflection may run.**"""
        return self._clock() - self._last_turn_at

    def take_remember_request(self) -> bool:
        """Whether the user asked to be remembered since this was last called.

        **Reading it clears it.** The caller is about to reflect, and leaving the flag set
        would make every later pass think it was asked too — a request answered once is
        answered.
        """
        asked = self._asked_to_remember
        self._asked_to_remember = False
        return asked

    @property
    def last_latency(self) -> TurnLatency | None:
        """The most recent turn's breakdown. **What the Inspector shows** (roadmap Phase 1)."""
        return self._last_latency

    def set_tts_speed(self, speed: float) -> None:
        """Uses a new Core-owned speed for the next scheduler that is created."""
        self._tts_speed = validate_speed(speed)

    def set_tts_volume(self, volume: float) -> None:
        """Uses a new Core-owned volume multiplier for the next scheduler that is created."""
        self._tts_volume = validate_volume(volume)

    def set_llm_model(self, model: str) -> None:
        """Uses a setup-selected model for subsequent turns without rebuilding the loop."""
        self._options = replace(self._options, model=model)

    # ── Entry point ──────────────────────────────────────────────

    async def run(self) -> None:
        """Consumes VAD events. **The entry point on the asyncio side.**

        **Each turn runs as a separate task.** `await`ing here would make it impossible
        to pick up a `SPEECH_STARTED` (i.e. a barge-in) arriving while Lumi is speaking.
        A loop that can't notice it was interrupted is the same as having no barge-in at all.

        **One broken event never ends the loop.** Letting it propagate makes Lumi deaf for
        the rest of the session — a far worse outcome than one dropped utterance — and
        asyncio would only reveal it at GC time. **Loud, and still listening.**
        """
        if self._audio is None or not self._audio.can_listen:
            log.warning("reactive.no_input")
            return

        async for notification in self._audio.events():
            event = notification.event
            if event is VadEvent.SPEECH_STARTED:
                self._stt.begin_turn()
                try:
                    await self.on_speech_started()
                except Exception:
                    log.exception("reactive.interrupt_failed")
            elif event is VadEvent.SILENCE_STARTED and notification.audio is not None:
                # **Never awaited.** The mute decision and the next VAD event must not queue
                # behind an inference (docs/architecture/audio.md §2)
                self._stt.speculate(notification.generation, notification.audio)
            elif event is VadEvent.SPEECH_ENDED and notification.audio is not None:
                spawn(
                    self.on_speech_ended(
                        notification.audio,
                        notification.audio_at,
                        generation=notification.generation,
                    ),
                    name="turn",
                    event="reactive.turn_crashed",
                    keep=self._turns,
                )

    async def shutdown(self) -> None:
        """Stop the turns still running. **Called before the databases close.**

        Cancelling the loop's own task ends the event stream but leaves the turns it
        started: a turn blocked on an uncancellable STT keeps going, and finishes by
        writing to resources that are being torn down. Each turn's cancellation is
        cooperative, so this waits for them rather than assuming they stopped.

        **The recall tallies are stopped too.** They are the same hazard — a write into a
        connection that is closing — and being not-a-turn is a reason to hold them apart,
        not a reason to leave them running.
        """
        turns = tuple(self._turns) + tuple(self._writes)
        for task in turns:
            task.cancel()
        for task in turns:
            with contextlib.suppress(BaseException):
                await task

    async def _show_user_said(self, text: str) -> None:
        """Put the user's utterance in a bubble. **Never costs a turn.**

        The Stage is expression only, so a failure to draw must not become a failure to
        answer — the reply is the thing that matters, the caption is not.
        """
        try:
            await self._notifier.notify(Role.STAGE, METHOD_USER_SAID, {"text": text})
        except Exception:
            log.warning("reactive.user_said_failed", exc_info=True)

    async def on_speech_started(self) -> None:
        """**Barge-in.** The sound has already stopped (VAD thread). Stop the Activity."""
        result = await self._arbiter.interrupt("user_speech")
        log.info(
            "reactive.interrupted",
            activity=str(result.activity_id),
            stopped=result.stopped,
            abandoned=result.abandoned,
        )

    async def on_speech_ended(
        self, audio: AudioBuffer, ended_at: float | None = None, *, generation: int = 0
    ) -> None:
        """A speech segment was confirmed. **Adopt or run STT before proposing an Activity.**

        STT happens before the proposal so that **no Activity gets created if nothing was
        actually said**. An empty conversation Activity needlessly preempts idle and
        clutters the Inspector.

        `ended_at` is when the user actually stopped talking, not when this was called.
        **The gap between the two is `vad_ms`** and it is the largest fixed cost in the
        budget (docs/architecture/audio.md §7), so it must not be measured from here.

        `generation` decides whether a speculation started during that gap describes *this*
        audio. **A mismatch is not an error** — it means the user carried on talking, and the
        confirmed buffer is transcribed instead (ADR-039).
        """
        timer = TurnTimer(new_correlation_id(), started_at=ended_at)
        timer.since_start("vad_ms")
        # **The timer's clock, not `perf_counter` directly.** `overlap_ms` subtracts
        # this from the STT runner's timestamps, so all three have to be one clock
        vad_ended_at = timer.now()
        # **Measured before STT runs**, so a segment that makes STT fail outright still
        # says something about what went in. Peak and RMS separate "the audio was already
        # damaged" from "the model got clean audio and still got it wrong" — the 48 kHz →
        # 16 kHz aliasing found on 2026-08-17 shows up here as a peak near 1.0 with speech
        # nowhere near that loud. **The audio itself is never written down**
        # (docs/contracts/privacy.md §6).
        log.info(
            "reactive.segment",
            seconds=round(len(audio) / SAMPLE_RATE, 2),
            peak=round(float(np.max(np.abs(audio))) if len(audio) else 0.0, 4),
            rms=round(float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0, 4),
        )
        try:
            outcome = await self._stt.resolve(generation, audio)
        except ProviderError as error:
            # **Don't silently ignore it.** What's missing is tracked by setup state
            log.warning("reactive.stt_failed", error=str(error))
            return

        record_stt(timer, outcome, vad_ended_at=vad_ended_at)
        text = outcome.transcription.text.strip()
        if not text:
            log.info("reactive.empty_transcription")
            return
        await self.handle_text(text, timer=timer)

    async def _transcribe(self, audio: AudioBuffer) -> Transcription:
        """One STT execution. **The Provider is resolved per execution**, so a speculation
        started before STT finished warming up still waits for the same provider everything
        else uses.
        """
        stt: STTProvider = await provider_of(self._providers, ProviderKind.STT)
        # `CancelToken` is passed for the interface's sake. **STT is `non_cancellable`**:
        # the inference runs in a thread and finishes regardless (ADR-039)
        return await stt.transcribe(audio, LANGUAGE, CancelToken())

    async def handle_text(self, text: str, *, timer: TurnTimer | None = None) -> None:
        """One turn from text input. **Takes the same path as the voice route.**

        Without a `timer`, one starts here — so `vad_ms` and `stt_ms` are simply absent
        rather than zero. **Typed input never went through those stages**, and recording
        them as 0 would drag the percentiles toward a speed the voice path never reaches.
        """
        timer = timer or TurnTimer(new_correlation_id())
        scales = VoiceScales(speed=self._tts_speed, volume=self._tts_volume)
        # **Noticed here, acted on later.** The answer to 「覚えておいて」 is the reply this
        # turn is about to give; the extraction is a Job, and putting it on this path
        # would be inference inside the turn the user is waiting on (ADR-018).
        if asked_to_remember(text):
            self._asked_to_remember = True
        await self._show_user_said(text)
        if self._retriever is None:
            # **Only when nothing will retrieve.** `begin()` ignores a span that is already
            # recorded, so writing this unconditionally — as Phase 1 did, when there was no
            # retrieval at all — made every turn report `retrieve_ms: 0` while the search
            # ran unmeasured. A span that is always zero is worse than a missing one: it
            # reads as "this costs nothing" rather than as "nobody looked".
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
            await self._turn.run(activity, text, timer, scales)
        except ProviderError as error:
            # **Record in the Activity's state that it failed to speak** (never silently mark it a
            # success)
            log.warning("reactive.turn_failed", error=str(error))
            failed = True
        finally:
            # **An interrupted turn reports too.** Barge-in is the normal case, and how far
            # the turn got before being cut off is exactly what needs measuring
            self._last_latency = timer.emit()
            self._last_turn_at = self._clock()
            if self._arbiter.current().id == activity.id:
                # If it was interrupted, the Arbiter has already cleaned up. Don't double-transition
                await self._arbiter.complete(activity.id, failed=failed)

    async def _recall(self, text: str, timer: TurnTimer) -> Sequence[ContextBlock]:
        """Memories worth having in front of this turn. **On the critical path.**

        Measured at ~20 ms for the query embedding plus the two index lookups
        (docs/measurements/phase2.md), which is why it is a span of its own rather than
        being absorbed into `assemble_ms`.

        **Failure costs the memories, not the turn.** Answering without a memory is a worse
        answer; not answering is a broken product. Counting what was recalled is a write,
        so it happens after the fact, off this path.
        """
        if self._retriever is None:
            return ()
        # **The span is the whole lookup**, not just the embedding: `RetrievalResult` also
        # reports `embed_ms`, and the difference between them is what the two indexes cost.
        with timer.span("retrieve_ms"):
            try:
                result = await self._retriever.retrieve(
                    text,
                    # **What the lines may spend**, with the frames already taken out.
                    # Two of them are reserved because `to_blocks` splits trusted from
                    # tainted memories, and which blocks exist is not known until after
                    # the search. **Over-reserving costs one memory; under-reserving
                    # overflows into the budget the conversation turns are packed from.**
                    token_budget=MEMORY_BUDGET_TOKENS - MAX_MEMORY_BLOCKS * BLOCK_OVERHEAD_TOKENS,
                    now=self._clock(),
                )
            except Exception as error:
                log.warning("memory.retrieve_failed", error=str(error))
                return ()
        if result.selected:
            spawn(
                self._retriever.record_use(result, now=self._clock()),
                name="memory.record_use",
                event="memory.record_use_failed",
                keep=self._writes,
            )
        return to_blocks(result.records)
