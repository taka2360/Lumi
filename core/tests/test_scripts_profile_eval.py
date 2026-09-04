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
from dataclasses import replace
from typing import Any

import httpx
import pytest

from lumi.agent.session import Session
from lumi.providers.base import ProviderUnavailable
from lumi.providers.llm.base import Finish, LLMEvent, LLMFailure, LLMOptions, TextDelta, ToolCall
from lumi.providers.llm.sampling import Purpose, options_for
from scripts import llm_profile_eval as module
from scripts.llm_profile_eval import (
    CASES,
    VARIANTS,
    WARM_UP_TURN,
    Reply,
    Sample,
    _render,
    exposed_tools,
    fenced,
    generate,
    model_identity,
    resolve_tag,
    warm_up,
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


async def test_a_warm_up_that_did_not_generate_stops_the_run() -> None:
    """★ **The one failure in the run that would otherwise be invisible.**

    `generate()` reports a broken stream as `Sample.FAILED` instead of raising, and the
    warm-up's output is thrown away by design — so an unchecked warm-up lets the report
    claim a cache state the run never reached, and the profile's first case pays a cost
    that reads as its settings. Every other failure prints itself as `除外`; this one has
    to be raised or it says nothing at all.
    """
    provider: Any = Scripted(LLMFailure(message="the engine died"))

    with pytest.raises(RuntimeError, match="warm-up failed"):
        await warm_up(provider, OPTIONS, exposed_tools())


@pytest.mark.parametrize(
    "events",
    [
        pytest.param((TextDelta(text='<|ACT {"emotion":"sad"}|>'), stop()), id="marker_only"),
        pytest.param(
            (
                TextDelta(text="うん"),
                ToolCall(id="1", name="character.set_expression", arguments={}),
                stop(),
            ),
            id="tool_call",
        ),
        pytest.param((TextDelta(text="うん"), Finish(reason="length")), id="truncated"),
    ],
)
async def test_a_warm_up_that_generated_is_a_warm_up(events: tuple[LLMEvent, ...]) -> None:
    """★ **The only question the warm-up asks is whether inference ran.** Its output is
    discarded by design, so a neutral turn answered with a bare marker, a tool call, or a
    reply that hit the cap still left the server's cache exactly as warm as intended.

    Aborting on those would throw away a ten-minute run because a throwaway turn said the
    wrong thing — the equalization it exists for had already happened.
    """
    provider: Any = Scripted(*events)

    await warm_up(provider, OPTIONS, exposed_tools())


async def test_a_marker_the_parser_refused_is_counted_not_hidden() -> None:
    """★ `MarkerStream` consumes a closed-but-invalid directive and returns no intent, so
    `marker 0` alone cannot tell **"emitted no marker"** from **"emitted broken ones"**.

    That distinction is a sampling result: malformed JSON is what a hotter profile
    produces, and the report presents the marker column as protocol adherence.
    """
    reply = await one(
        TextDelta(text='ふむ<|ACT {"emotion":"nonexistent"}|>'),
        TextDelta(text='そうだね<|ACT {"emotion":"happy"}|>'),
        stop(),
    )

    assert reply.text == "ふむそうだね"
    assert reply.intents == 1
    assert reply.malformed_markers == 1


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param('<|ACT {"emotion":', id="opened_and_abandoned"),
        pytest.param("<|A", id="stopped_on_a_partial_open"),
    ],
)
async def test_a_marker_left_unfinished_on_a_clean_stop_is_a_failure(tail: str) -> None:
    """★ **A correction.** This was left uncounted as "a truncation artefact, and those
    samples are excluded anyway" — true only when the ending was `length`.

    A reply that stops mid-directive and then finishes `stop` is a sample that *counts*,
    `flush()` removes the fragment, and nothing else in the report mentions it. Without
    this, that turn is indistinguishable from one that emitted no marker at all.
    """
    reply = await one(TextDelta(text=f"まあね{tail}"), stop())

    assert reply.status is Sample.OK
    assert reply.text == "まあね"  # the fragment is never spoken
    assert reply.intents == 0
    assert reply.malformed_markers == 1


async def test_a_reply_with_only_good_markers_reports_none_broken() -> None:
    """The other side: the column stays quiet when there is nothing to say."""
    reply = await one(TextDelta(text='やあ<|ACT {"emotion":"happy"}|>'), stop())

    assert reply.intents == 1
    assert reply.malformed_markers == 0


def test_a_broken_marker_on_an_early_turn_reaches_the_report() -> None:
    """★ Only the last turn's numbers are printed, so a case-long protocol failure would
    otherwise be visible for turn 3 alone. **A directive the parser refused on turn 1 is
    still a directive the parser refused** — and the report calls this column adherence.
    """
    early = Reply(
        text="うん",
        status=Sample.OK,
        detail="",
        first_token_ms=1,
        total_ms=2,
        tokens=3,
        prompt_tokens=4,
        intents=0,
        malformed_markers=2,
    )
    clean = replace(early, malformed_markers=0, intents=1)

    block = "\n".join(_render(MULTI, [early, clean, clean]))

    assert "壊れ 2" in block
    # And the parsed ones are the case's total too, not just the last turn's
    assert "marker 2" in block


def test_a_case_with_no_broken_markers_says_nothing_about_them() -> None:
    """`壊れ 0` on every line is noise. The column appears when there is a finding."""
    clean = Reply(
        text="うん",
        status=Sample.OK,
        detail="",
        first_token_ms=1,
        total_ms=2,
        tokens=3,
        prompt_tokens=4,
        intents=1,
    )

    assert "壊れ" not in "\n".join(_render(SINGLE, [clean]))


async def test_an_engine_error_costs_one_cell_not_the_run() -> None:
    """★ `OllamaProvider.stream()` **raises** rather than yielding `LLMFailure` when the
    request never got started (an HTTP error status, a version mismatch).

    Left to propagate, that unwinds `main()` past the `write_text` at the end — so one
    transient 500 late in a ten-minute run discards every variant already measured. It is
    the same outcome the harness already documents for a broken stream, so it gets the
    same treatment: an excluded cell, and the run carries on.
    """

    class Exploding:
        """Raises on the first step, the way `stream()` does when the POST came back 4xx."""

        def stream(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            return self

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> LLMEvent:
            raise ProviderUnavailable("ollama_http_error", "500 boom")

    provider: Any = Exploding()
    reply = await generate(provider, Session(), OPTIONS, SINGLE.turns[0], exposed_tools())

    assert reply.status is Sample.FAILED
    assert "500 boom" in reply.detail


async def test_a_generation_that_never_stops_is_cut_off_and_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ **Variant A sends no `num_predict`, deliberately**, and httpx's timeout fires on
    silence between reads — never on a model that streams forever. Production would revoke
    the token when the foreground wanted the GPU (ADR-018); a harness has no foreground.

    So the deadline fires the token, the provider stops at its next checkpoint, and the
    stream ends without a `Finish` — which is already `FAILED`.
    """
    monkeypatch.setattr(module, "GENERATION_DEADLINE_S", 0.0)

    class Endless:
        async def stream(
            self, messages: Any, tools: Any, options: Any, cancel_token: Any
        ) -> AsyncIterator[LLMEvent]:
            del messages, tools, options
            while not cancel_token.is_set:
                yield TextDelta(text="あ")

    provider: Any = Endless()
    reply = await generate(provider, Session(), OPTIONS, SINGLE.turns[0], exposed_tools())

    assert reply.status is Sample.FAILED
    assert "still generating" in reply.detail


def test_a_reply_cannot_break_out_of_its_own_cell() -> None:
    """★ The `technical` case asks about `git rebase`, so a reply with a code fence is an
    ordinary Tuesday — and one that never closes it swallows **the rest of the report**:
    every later case and variant rendered inside that block.

    Quoting bounds it: the fence is closed by the end of the quote, so the malformed
    output stays visible and stays in its own cell.
    """
    runaway = Reply(
        text="こうします:\n```python\nrebase()",  # opened, never closed
        status=Sample.OK,
        detail="",
        first_token_ms=1,
        total_ms=2,
        tokens=3,
        prompt_tokens=4,
        intents=1,
    )

    block = _render(SINGLE, [runaway])

    body = [line for line in "\n".join(block).splitlines() if "rebase()" in line]
    assert body == ["> rebase()"]
    # **Still readable.** Bounding it must not mean hiding it
    assert any("```python" in line for line in "\n".join(block).splitlines())


def test_a_model_file_full_of_backticks_stays_inside_its_block() -> None:
    """The same rule for text a *server* wrote. `/api/show` returns whatever the Modelfile
    holds, and a three-backtick run in it would end the block early.
    """
    fence = fenced('stop "```"\ntemperature 1')

    assert fence.startswith("````")
    assert fence.endswith("````")


def test_the_vague_case_has_something_to_be_vague_about() -> None:
    """★ Its expectation is that the reply follows the preceding topic. `main()` opens a
    fresh `Session` per case and there are no memories, so **as a lone utterance there is
    no preceding topic** — the expectation would be unmeetable and unfalsifiable at once,
    and a reader could not tell context-following from a lucky guess.
    """
    vague = next(case for case in CASES if case.name == "vague")

    assert len(vague.turns) > 1


def _metadata(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_the_report_records_what_the_tag_actually_was() -> None:
    """★ **A tag is mutable, and variant A is now defined by whatever it holds.**

    A publishes `temperature` and nothing else, so the rest comes from this model's own
    file — the honest way to reproduce the pre-ADR-048 request, and, unrecorded, the way
    to lose the baseline. `qwen3.5:9b` re-pulled next year is a different set of numbers
    under the same name, and "not sent" no longer says what the comparison ran against.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"parameters": "top_p 0.95\npresence_penalty 1.5"})
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b", "digest": "abc123"}]})

    report = "\n".join(await model_identity("qwen3.5:9b", transport=_metadata(handler)))

    assert "abc123" in report
    # **Verbatim.** Reading it into a dict would be one more place to misread it
    assert "presence_penalty 1.5" in report


@pytest.mark.parametrize(
    ("asked", "listed", "expected"),
    [
        pytest.param("qwen3.5:9b", ["qwen3.5:9b"], "qwen3.5:9b", id="exact"),
        pytest.param("qwen3.5", ["qwen3.5:latest"], "qwen3.5:latest", id="tagless_to_latest"),
        pytest.param("qwen3.5", ["qwen3.5:9b"], "qwen3.5:9b", id="tagless_to_the_only_tag"),
        pytest.param("qwen3.5", ["qwen3.5:9b", "qwen3.5:4b"], "", id="tagless_but_ambiguous"),
        pytest.param("qwen3.5", ["gemma3:12b"], "", id="not_there"),
    ],
)
def test_a_tagless_model_name_still_identifies_its_weights(
    asked: str, listed: list[str], expected: str
) -> None:
    """★ **`--model qwen3.5` is a name Ollama serves and `/api/tags` never lists.**
    `OllamaProvider._has_model` matches the tag-less form deliberately and `is_qwen3()`
    accepts it, so an exact lookup here records `(unknown)` for a perfectly ordinary run —
    losing the one field that identifies the mutable thing it used.

    **Ambiguity resolves to nothing**: a digest naming the wrong weights is worse evidence
    than no digest.
    """
    entries = [{"name": name, "digest": f"sha-{name}"} for name in listed]

    tag, digest = resolve_tag(asked, entries)

    assert tag == expected
    assert digest == (f"sha-{expected}" if expected else "")


async def test_the_report_records_the_engine_that_decoded_it() -> None:
    """★ Variant A is made of fall-throughs. A field its request omits that the model file
    does not set either lands on **Ollama's** default — and `sampling.py` says outright
    that those are not to be taken on a runtime's word ("0.33.2's own default is already
    1.0 ... but a value this load-bearing is not left to a runtime's release notes").

    So the digest alone does not identify A's baseline; the engine version is half of it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"parameters": "top_p 0.95"})
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.33.2"})
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b", "digest": "abc123"}]})

    report = "\n".join(await model_identity("qwen3.5:9b", transport=_metadata(handler)))

    assert "0.33.2" in report


async def test_a_tag_that_cannot_be_read_is_a_caveat_not_a_crash() -> None:
    """The comparison is still valid for the machine it ran on, so the run continues —
    **but the report has to say it can no longer name A's baseline.**
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, text="boom")

    report = "\n".join(await model_identity("qwen3.5:9b", transport=_metadata(handler)))

    assert "⚠" in report
    assert "復元できない" in report


def test_the_before_variant_names_no_value_it_did_not_send() -> None:
    """★ **A is the pre-ADR-048 request, and that request set one field.**

    Writing `qwen3.5:9b`'s inherited values into A would pin the baseline to one tag while
    `is_qwen3()` admits the whole family — pointed at a sibling whose file differs, A would
    be a request Lumi never sent, and every difference measured against it would be a
    difference from a fabricated past. Omission reproduces A exactly, for any model.
    """
    label, overrides = VARIANTS[0]
    assert "before" in label
    assert overrides["temperature"] == 0.8
    named = {field for field, value in overrides.items() if value is not None}
    assert named == {"temperature"}


def test_every_other_variant_starts_from_the_shipped_profile() -> None:
    """B–E are the family's profile with one field moved, so they may name values — those
    values are `sampling.py`'s, not a model file's. **A is the only one reproducing a past.**
    """
    for _, overrides in VARIANTS[1:]:
        assert None not in overrides.values()
