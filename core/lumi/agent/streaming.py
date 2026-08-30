"""One LLM stream, into **what was said** and **what was asked for.**

Design → docs/architecture/agent.md §3

A stream arrives as tokens and leaves as two different things: sentences handed to TTS as
soon as each one is complete, and tool calls collected for the step after this one. The
`match` that separates them was written inline in the middle of the turn, where the four
cases — text, reasoning, a tool call, a failure — read as one long branch rather than as
four decisions that have nothing to do with each other.

## Why each sentence is spoken as it completes

**The first word has to arrive before the last token does** (docs/architecture/audio.md §7).
Collecting the reply and then speaking it would satisfy every assertion about *what* Lumi
said while making her wait for the whole answer before starting to say it, which is the
difference between a conversation and a form submission.

## A stream that breaks does not raise

`LLMFailure` mid-stream is logged and ends the stream. **What has already been spoken has
to stay consistent** with what the session records: raising here would leave the user
having heard sentences that no history contains.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from lumi import logging as lumi_logging
from lumi.agent.latency import TurnTimer
from lumi.agent.markers import MarkerStream
from lumi.agent.sentences import SentenceStream
from lumi.agent.speech import PlaybackScheduler
from lumi.character import ExpressionIntent
from lumi.kernel.cancellation import CancelToken
from lumi.providers.llm.base import (
    Finish,
    LLMEvent,
    LLMFailure,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)

log = lumi_logging.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Spoken:
    """What one stream produced.

    `reply` is **the text with the markers taken out** — what Lumi said, not what the model
    emitted. The markers themselves have already been acted on by the time this is returned.
    """

    reply: str
    calls: list[ToolCall]


async def consume_stream(
    events: AsyncIterator[LLMEvent],
    *,
    scheduler: PlaybackScheduler,
    timer: TurnTimer,
    cancel_token: CancelToken,
    express: Callable[[ExpressionIntent], Awaitable[None]],
) -> Spoken:
    """Drains one stream, speaking as it goes.

    The two timer spans are the ones the user can feel: `llm_first_token_ms` ends when
    anything at all arrives, and `llm_first_segment_ms` — which only starts once there is
    a first token — ends when the first thing worth speaking does. **`end` is idempotent**,
    so the per-sentence calls after the first are free.

    Stops at the next event once `cancel_token` is set: the stream contract is
    `cooperative`, so a barge-in is honoured at a checkpoint rather than mid-token.
    """
    markers = MarkerStream()
    sentences = SentenceStream()
    spoken: list[str] = []
    calls: list[ToolCall] = []

    timer.begin("llm_first_token_ms")
    async for event in events:
        if cancel_token.is_set:
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
                    await express(intent)
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

    return Spoken(reply="".join(spoken), calls=calls)
