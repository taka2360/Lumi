"""The A/B harness's sample classifier → `core/scripts/llm_profile_eval.py`.

**The harness itself calls an LLM; deciding what to do with the answer does not.** That
split is the whole reason there is anything to test here: `docs/measurements/phase2.md`
cites this script as evidence, and evidence that quietly counts a truncated, empty or
marker-only generation as a reply is evidence for the wrong thing.

Three rounds of review have landed on exactly this classifier. What each case below pins
down is a way a generation can look like a short, fast reply without being one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from lumi.agent.session import Session
from lumi.providers.llm.base import Finish, LLMEvent, LLMFailure, LLMOptions, TextDelta, ToolCall
from lumi.providers.llm.sampling import Purpose, options_for
from scripts.llm_profile_eval import (
    CASES,
    WARM_UP_TURN,
    Reply,
    Sample,
    exposed_tools,
    generate,
)

OPTIONS = options_for("qwen3.5:9b", Purpose.CONVERSATION)
#: A case with one turn, and the multi-turn one drift is measured on
SINGLE, MULTI = CASES[0], CASES[2]


def stop() -> Finish:
    return Finish(reason="stop", usage={"completion_tokens": 5, "prompt_tokens": 300})


class Scripted:
    """An `LLMProvider` that replays fixed events. **Asserts the request shape**, because
    the tool descriptors are half of what makes this harness a measurement of production.
    """

    def __init__(self, *events: LLMEvent) -> None:
        self._events = events
        self.prompts: list[Sequence[Any]] = []

    async def stream(
        self, messages: Sequence[Any], tools: Any, options: LLMOptions, cancel_token: Any
    ) -> AsyncIterator[LLMEvent]:
        assert [tool.name for tool in tools] == ["character.set_expression"]
        self.prompts.append(messages)
        for event in self._events:
            yield event


async def one(*events: LLMEvent, text: str = SINGLE.turns[0]) -> Reply:
    # The fake is a stand-in for `stream()` alone; the rest of `Provider` is
    # load/unload bookkeeping this never reaches (same convention as the other LLM fakes)
    provider: Any = Scripted(*events)
    return await generate(provider, Session(), OPTIONS, text, exposed_tools())


async def test_the_model_sees_what_production_sends_it() -> None:
    """★ Every shipped turn passes `ToolRegistry.list_exposed()`. A request without the
    `tools` field is a different request, and its sampling is a different sampling.
    """
    assert [tool.name for tool in exposed_tools()] == ["character.set_expression"]


async def test_expression_markers_are_not_counted_as_speech() -> None:
    """★ `SPEECH_PROTOCOL` asks for `<|ACT ...|>` on every turn, and a marker is ~40 ASCII
    characters inside a 30-120 character Japanese reply. Left in, it inflates 字数 and drags
    反復 toward whatever the marker repeats — **and profiles emit markers at different
    rates**, so it is not a constant offset between variants.

    Split across two deltas on purpose: that is how it arrives on the wire.
    """
    reply = await one(
        TextDelta(text='おつかれ。<|ACT {"emotion":"ha'),
        TextDelta(text='ppy","intensity":0.7}|>ゆっくりしなよ'),
        stop(),
    )

    assert reply.status is Sample.OK
    assert reply.text == "おつかれ。ゆっくりしなよ"
    # Reported rather than hidden: how far each profile follows the protocol is a finding
    assert reply.intents == 1


async def test_an_unterminated_marker_is_dropped_not_spoken() -> None:
    """The same rule `agent/markers.py` holds to. **Never read half-parsed.**"""
    reply = await one(TextDelta(text='うん<|ACT {"emo'), stop())

    assert reply.text == "うん"


@pytest.mark.parametrize(
    "events",
    [
        pytest.param((TextDelta(text='<|ACT {"emotion":"sad"}|>'), stop()), id="marker_only"),
        pytest.param((TextDelta(text="  \n "), stop()), id="whitespace_only"),
        pytest.param((stop(),), id="nothing_at_all"),
    ],
)
async def test_a_clean_finish_with_nothing_to_say_is_not_a_reply(events: tuple[LLMEvent]) -> None:
    """★ **Silence, not brevity.** Counted as a sample it reports as the shortest and
    fastest generation of the run, and the profile that produced it reads as admirably
    terse. In production that turn is Lumi saying nothing at all.
    """
    reply = await one(*events)

    assert reply.status is Sample.EMPTY


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        pytest.param(
            (TextDelta(text="うん"), Finish(reason="length")), Sample.TRUNCATED, id="length"
        ),
        pytest.param(
            (TextDelta(text="うん"), Finish(reason="guardrail")), Sample.UNKNOWN, id="unread"
        ),
        pytest.param((TextDelta(text="うん"),), Sample.FAILED, id="no_finish"),
        pytest.param(
            (TextDelta(text="うん"), LLMFailure(message="died")), Sample.FAILED, id="failure"
        ),
        pytest.param(
            (
                TextDelta(text="うん"),
                ToolCall(id="1", name="character.set_expression", arguments={}),
                stop(),
            ),
            Sample.TOOL_CALL,
            id="tool_call",
        ),
    ],
)
async def test_every_other_ending_is_excluded(events: tuple[LLMEvent], expected: Sample) -> None:
    """★ Variant A sends no `max_tokens` and B-E send 512, so a capped sample would read as
    "shorter and faster" for a reason that is not its sampling. The other endings are not
    replies at all — a tool call is one step of a turn production would continue.
    """
    reply = await one(*events)

    assert reply.status is expected
    assert reply.detail


async def test_an_excluded_turn_takes_the_rest_of_its_case_with_it() -> None:
    """★ Drift shows up on turn 3. A case whose turn 1 was cut off carries a different
    conversation into turns 2 and 3 than the variant that held the whole one — and
    recording the half-reply as history is how that spreads without anyone seeing it.
    """
    from scripts.llm_profile_eval import _run_case

    provider = Scripted(TextDelta(text="うん"), Finish(reason="length"))
    session = Session()

    replies = await _run_case(
        provider,  # type: ignore[arg-type]
        session,
        OPTIONS,
        MULTI,
        exposed_tools(),
    )

    assert len(MULTI.turns) == 3
    assert [reply.status for reply in replies] == [Sample.TRUNCATED]
    assert len(provider.prompts) == 1


def test_the_warm_up_turn_is_not_one_of_the_cases() -> None:
    """★ The warm-up runs immediately before a profile's first case. If it generated that
    case's own prompt, that profile alone would reach it with the user message already in
    the KV cache — a head start on exactly one printed cell, on the same variant every run.
    """
    assert WARM_UP_TURN not in {turn for case in CASES for turn in case.turns}
