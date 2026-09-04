"""A/B harness for sampling profiles. **Dev-time only; never imported by Core.**

    uv run python scripts/llm_profile_eval.py --out ../eval.md

Why a script and not a test: `.claude/rules/python-core.md` says Core must be testable
without calling an LLM. **This calls one on purpose** — the question it answers ("does
Japanese come out better") has no assertion, only output a person reads.

## What it holds fixed

Everything except the profile. The same five cases, the same seed per case, the **real**
`assemble()` over the **real** Content Pack persona and `SPEECH_PROTOCOL`, and the **real**
tool descriptors from `build_tool_registry()` — a persona-drift comparison run against a
hand-written prompt, or against a request with no `tools` field, would be measuring a
request shape that is not the one Lumi sends.

`seed` is what makes the comparison a comparison: without it, two profiles differ by
sampling noise as much as by their settings, and five cases is nowhere near enough to
average that out. **Production never sets one** (`LLMOptions.seed`).

One thing production has that this does not: **recalled memories.** There is no memory
database here, so every prompt goes out with no `ContextBlock`s and is shorter than a real
turn's. Every variant is equally without them, so the comparison holds; the absolute
prompt-token column does not describe a production turn.

## A sample counts only if it ended the way a reply ends

`Finish(reason="stop")`, with something left to say once `MarkerStream` has taken the
`<|ACT ...|>` directives out — **the same text production records and speaks**. Everything
else is **excluded from the comparison and printed as excluded**, because every other
ending measures something other than the profile:

| ending | why it is not a sample |
|---|---|
| `length` | the reply stopped where `max_tokens` was, not where the model was done. |
| | Variant A sets no cap at all, so a capped variant would read as "shorter and |
| | faster" for a reason that is not its sampling |
| a tool call | production would run it and generate again; the text and the latency |
| | here are one step of a turn, not a turn |
| empty | `stop` with nothing spoken is silence, not brevity — and after markers |
| | are stripped, a turn that emitted only `<|ACT ...|>` lands here |
| `LLMFailure` / no `Finish` | there is no reply to measure |
| a `ProviderError` | the request never started (HTTP 4xx/5xx). **Costs one cell, |
| | not the run** — the exception would otherwise unwind past the |
| | `write_text` at the end and discard every variant already measured |
| `GENERATION_DEADLINE_S` | the model was still going. **A is uncapped on purpose and |
| | httpx only times out on silence**, so this is what bounds the run |
| any other reason | an unread ending is not assumed to be whole — |
| | `memory/reflection.py` takes the same line |

**An invalid sample takes the rest of its case with it.** The cases are multi-turn on
purpose — drift shows up on turn 3 — and a turn that is missing or cut off makes the
turns after it a different conversation from the one the other variants held. Recording
it as an ordinary assistant reply is how that contamination would spread silently.

**The termination policy is deliberately asymmetric** and the report says so: variant A is
the pre-ADR-048 request, which set no `num_predict` at all. Making A capped to match B–E
would compare the shipped profile against something that never shipped. Exclusion is what
keeps that asymmetry from turning into a false result.

**A is reproduced by leaving fields off, never by naming what they became.** The values a
model file supplies belong to one tag, and `is_qwen3()` admits a family — so a written-out
baseline would be a request Lumi never sent as soon as the flag pointed at a sibling. See
`VARIANTS`.

## What it does not do

**It does not score.** Naturalness of Japanese is not a number this repo can compute, and
a made-up scorer would launder taste into evidence. What is counted is only what counting
answers honestly: length, latency, and how much of the reply is literally repeated text.

**It does not execute tools.** The model is shown the same descriptors production shows
it, which is what changes the request and the sampling distribution. Running the call
would need the Permission Kernel, an audit database and a Stage to send the expression to,
and it would add a second generation to a number that is supposed to describe one — so a
tool call is reported and excluded instead (`Sample.TOOL_CALL`). The registry built here
is for `list_exposed()` only; **nothing in this file calls `invoke`.**

**It does not run on a model family it has no variants for.** `VARIANTS` B–E are Qwen's
own numbers with one field moved; pointed at `gemma3:12b` those labels would be lies about
a base that `options_for()` had already fallen back to temperature-only. A new family needs
its own variant table, not a `--model` flag.

**It does not verify the server it is talking to.** `OLLAMA_NUM_PARALLEL` decides whether
the warm-up equalizes anything at all (`warm_up`), and it belongs to a process that may not
be on this machine — so the report states that precondition rather than checking it. What
the run *can* pin down about its environment, it records: the model digest and `/api/show`'s
parameter block (`model_identity`), because a tag is mutable and variant A is defined by it.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from lumi.agent.markers import MARKER_CLOSE, MARKER_OPEN, MarkerStream
from lumi.agent.prompt import assemble
from lumi.agent.runtime import build_tool_registry
from lumi.agent.session import Session
from lumi.content.pack import load_character
from lumi.kernel.cancellation import CancelToken
from lumi.kernel.event import DomainEventDraft, EventBus
from lumi.kernel.ids import EventId
from lumi.permission.grants import GrantStore
from lumi.permission.kernel import PermissionKernel
from lumi.provenance import TrustLevel
from lumi.providers.base import ProviderError
from lumi.providers.llm.base import (
    Finish,
    LLMFailure,
    LLMOptions,
    LLMProvider,
    TextDelta,
    ToolCall,
)
from lumi.providers.llm.endpoint import DEFAULT_PORT, HOST
from lumi.providers.llm.ollama import OllamaProvider
from lumi.providers.llm.sampling import Purpose, is_qwen3, options_for
from lumi.tools.base import ToolDescriptor

#: Repo root → the Content Pack that ships with Lumi
PACK = Path(__file__).resolve().parents[2] / "content" / "characters" / "lumi"

#: One seed per case index, offset by `--seed-base`. **Fixed, so a rerun compares to the
#: same run** — and changeable, because five cases on one seed is an anecdote. Re-run on a
#: second base before believing a difference.
DEFAULT_SEED_BASE = 1000


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    #: User turns, in order. **More than one is the persona-stability case** — drift shows
    #: up on turn 3, not turn 1
    turns: tuple[str, ...]
    expectation: str


CASES: tuple[Case, ...] = (
    Case("daily", ("今日はちょっと疲れた",), "自然。大げさでない。翻訳調でない"),
    Case("short_question", ("明日の予定どうしようかな",), "無駄に長くならない"),
    Case(
        "persona",
        ("ねえ、いま何してた？", "ふーん。私は今日ずっと仕事してた", "そっちはどう思う？"),
        "人格と文体を数ターン維持する。説明口調に戻らない",
    ),
    Case("technical", ("Git の rebase と merge の違いを簡単に教えて",), "正確で、崩れない"),
    # ★ **Two turns, because the expectation needs something to be measured against.**
    # `main()` opens a fresh `Session` per case and there are no memories, so as a lone
    # utterance 「うーん、どうしよ」 arrives with no preceding topic at all — "文脈を踏まえる"
    # would then be asking the model to use context that was never in the prompt, and a
    # reader could not tell a profile that followed context from one that guessed well.
    Case(
        "vague",
        ("来週プレゼンなんだよね", "うーん、どうしよ"),
        "直前の話題（プレゼン）を引き継ぐ。一般論を始めない",
    ),
)


class Sample(StrEnum):
    """Whether a generation is a measurement of the profile, or of something else.

    **Only `OK` goes into the comparison.** The rest are printed with their reason so the
    report says why a cell is empty rather than quietly having one fewer sample in it.
    """

    #: `Finish(reason="stop")`, text only. **The only comparable outcome**
    OK = "ok"
    #: `Finish(reason="length")` — cut off at `max_tokens`, not finished
    TRUNCATED = "truncated"
    #: Finished cleanly with nothing to say. **Silence, not brevity**
    EMPTY = "empty"
    #: The model called a tool. Production would run it and generate again
    TOOL_CALL = "tool_call"
    #: `LLMFailure`, or a stream that ended without a `Finish`
    FAILED = "failed"
    #: A `Finish.reason` this script has not read. **Never assumed to be whole**
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reply:
    #: **What Lumi would have said** — markers stripped, as `agent/streaming.py` records it
    text: str
    #: **What this generation is worth as a measurement.** Read before any number below
    status: Sample
    #: Why, when `status` is not `OK`. Empty otherwise
    detail: str
    first_token_ms: int
    total_ms: int
    #: Reported by Ollama. `0` when the stream ended without a `Finish`
    tokens: int
    #: What the assembled prompt actually cost. **Not the estimate** `agent/prompt.py` budgets
    #: against — the point of printing it is to see how far apart the two are
    prompt_tokens: int
    #: `<|ACT ...|>` markers that parsed. **Reported, not measured against**: it says how
    #: far each profile follows `SPEECH_PROTOCOL`, and it is what the 字数 column would
    #: have been inflated by
    intents: int = 0
    #: ★ Markers the model **closed but `parse_marker` refused** — broken JSON, or an
    #: emotion not in `Emotion`. Counted apart because `MarkerStream` drops those without
    #: telling anyone: with only `intents`, a profile that emitted five unusable markers
    #: reads exactly like one that emitted none, and the column above claims to be about
    #: protocol adherence. **Malformed JSON is a sampling result** — it is what a hotter
    #: profile produces — so it is the kind of difference this harness exists to show
    malformed_markers: int = 0


class _NullAudit:
    """The audit log of a registry that never authorizes anything. See `exposed_tools()`."""

    async def append(self, record: Any) -> None:
        del record


class _NullEvents:
    """Likewise for the bus. Nothing here publishes a DomainEvent."""

    async def append(self, event_id: EventId, draft: DomainEventDraft, occurred_at: Any) -> int:
        del event_id, draft, occurred_at
        return 0


async def _never_sent(intent: Any) -> None:
    """`character.set_expression`'s exit point. **Unreachable**: nothing calls `invoke`."""
    raise AssertionError(f"the harness does not execute tools ({intent})")


def exposed_tools() -> Sequence[ToolDescriptor]:
    """The descriptors production puts on every conversation request.

    Built through `build_tool_registry()` rather than written out here, so a tool added to
    Lumi shows up in the measurement without anyone remembering to add it twice — a
    hand-kept copy would drift, and the drift would be read as a sampling result.

    The Permission Kernel and the bus it is given are inert stand-ins, because
    `list_exposed()` reaches neither: **this registry exists to describe, not to execute.**
    Wiring a real audit database in would suggest otherwise.
    """
    registry = build_tool_registry(
        permission=PermissionKernel(GrantStore(), _NullAudit()),
        bus=EventBus(_NullEvents()),
        send_expression=_never_sent,
    )
    return registry.list_exposed()


async def generate(
    provider: LLMProvider,
    session: Session,
    options: LLMOptions,
    text: str,
    tools: Sequence[ToolDescriptor],
) -> Reply:
    """One turn, taking the same path a real turn takes: `assemble()` then `stream()`.

    **The reply reaches the session only when it is a reply** — `Sample.OK`. A truncated
    or failed generation recorded as history would follow the case into its next turn and
    show up there as a persona result.
    """
    session.record_user_utterance(text)
    prompt = assemble(persona=load_character(PACK).persona, session=session)

    started = time.perf_counter()
    first: float | None = None
    # **The same parser production speaks through** (`agent/streaming.py`). `SPEECH_PROTOCOL`
    # asks for `<|ACT {...}|>` on every turn, and a marker is ~40 ASCII characters inside a
    # 30-120 character Japanese reply: left in, it lands in the 字数 column, drags
    # `repeated_ratio` toward whatever punctuation the marker repeats, and prints control
    # syntax as if Lumi had said it. **Profiles emit markers at different rates**, so that
    # is not a constant offset — it is a difference between variants that is not sampling.
    markers = MarkerStream()
    parts: list[str] = []
    # The stream as it arrived, **before the marker parser saw it.** Kept only so the
    # markers the parser threw away can be counted (`Reply.malformed_markers`)
    raw: list[str] = []
    intents = 0
    tokens = 0
    prompt_tokens = 0
    # **Starts invalid.** A stream that ends without a `Finish` has not said it is done,
    # and the joined text of one is indistinguishable from a whole short answer
    status = Sample.FAILED
    detail = "the stream ended without a Finish"
    # ★ **The one thing that can stop variant A.** A sends no `num_predict` on purpose,
    # and httpx's timeout is per-read — it fires on silence, never on a model that streams
    # forever. Nothing else here bounds that: production would revoke this token when the
    # foreground wanted the GPU (ADR-018), and a harness has no foreground. So the deadline
    # fires it, the provider stops at its next line, and the stream ends without a `Finish`
    # — which the initial `status` above already calls `FAILED`.
    token = CancelToken()
    deadline = started + GENERATION_DEADLINE_S
    try:
        async for event in provider.stream(prompt.messages, tools, options, token):
            if time.perf_counter() > deadline and not token.is_set:
                token.fire("deadline")
                detail = f"still generating after {GENERATION_DEADLINE_S:.0f} s"
            if isinstance(event, TextDelta):
                if first is None:
                    # **Before the marker parser, as production times it.** A delta that
                    # turns out to be the opening of a marker is still the moment the model
                    # started answering (`llm_first_token_ms` in `agent/streaming.py`)
                    first = time.perf_counter()
                raw.append(event.text)
                chunk = markers.feed(event.text)
                parts.append(chunk.text)
                intents += len(chunk.intents)
            elif isinstance(event, ToolCall):
                # Recorded, not run. **Latency and length describe one step of a turn**
                status, detail = Sample.TOOL_CALL, f"the model called {event.name}"
            elif isinstance(event, Finish):
                tokens = event.usage.get("completion_tokens", 0)
                prompt_tokens = event.usage.get("prompt_tokens", 0)
                status, detail = _ending(event.reason, status, detail)
            elif isinstance(event, LLMFailure):
                status, detail = Sample.FAILED, event.message
    except ProviderError as error:
        # ★ **An engine that answered 500 costs one cell, not the run.** `stream()` raises
        # rather than yielding `LLMFailure` when the request never got started (an HTTP
        # error status, a version mismatch), and left to propagate that exception unwinds
        # `main()` past the `write_text` at the end — **discarding every variant already
        # measured** for one transient failure late in a ten-minute run. It is the same
        # outcome the harness already documents for a broken stream, so it is reported the
        # same way: a `FAILED` cell, excluded and printed as excluded.
        #
        # A genuinely dead engine still stops things loudly: `warm_up` runs before every
        # variant and raises on `FAILED`.
        status, detail = Sample.FAILED, f"{error.reason}: {error}"
    ended = time.perf_counter()

    # **Whatever was left holding an unterminated marker is discarded**, exactly as the
    # turn does — half a marker is not something Lumi says
    parts.append(markers.flush())
    reply = "".join(parts)
    if status is Sample.OK and not reply.strip():
        # ★ **`stop` with nothing to say is not a concise reply, it is silence.** Counted
        # as `OK` it would report as a zero-length, fastest-of-the-run sample and make the
        # profile that produced it look admirably terse. It also matters more now that
        # markers are stripped: a turn that emitted only `<|ACT ...|>` lands here.
        status, detail = Sample.EMPTY, f"stop with no spoken text (markers: {intents})"
    if status is Sample.OK:
        session.record_lumi_turn(reply, TrustLevel.TRUSTED)
    return Reply(
        text=reply,
        status=status,
        detail=detail,
        first_token_ms=round(((first or ended) - started) * 1000),
        total_ms=round((ended - started) * 1000),
        tokens=tokens,
        prompt_tokens=prompt_tokens,
        intents=intents,
        # Markers the model started, minus the ones the parser could actually use.
        # **Unterminated openings are in the first number** — see `markers_written`
        malformed_markers=max(0, markers_written("".join(raw)) - intents),
    )


def markers_written(text: str) -> int:
    """How many `<|ACT ...|>` the model **started**, closed or not.

    Scans the way `MarkerStream.feed` scans — an open, then the next close after it — so
    the counts line up with the intents it produced. `parse_marker` returning `None` is
    invisible from outside the parser (the marker is consumed and no intent appears), and
    so is `flush()` throwing away an unterminated one. **This is the only way to tell
    "followed the protocol badly" from "did not follow it at all".**

    ★ **An unterminated opening counts, and that is a correction.** It was left out as "a
    truncation artefact, and those samples are excluded anyway" — which is only true when
    the ending was `length`. A reply that stops mid-marker and then finishes `stop` is a
    sample that counts, and half a directive in it is a protocol failure like any other.
    On a truncated sample the extra count is harmless: `_render` prints no numbers for an
    excluded one.
    """
    count = 0
    index = 0
    while (start := text.find(MARKER_OPEN, index)) >= 0:
        count += 1
        end = text.find(MARKER_CLOSE, start + len(MARKER_OPEN))
        if end < 0:
            return count  # opened and never closed — `flush()` drops it
        index = end + len(MARKER_CLOSE)
    # The stream may instead have stopped on a *partial* open (`<|A`), which `flush()`
    # discards as a marker too. Same test as `markers._partial_open_length`.
    tail = text[index:]
    for length in range(min(len(MARKER_OPEN) - 1, len(tail)), 0, -1):
        if tail.endswith(MARKER_OPEN[:length]):
            return count + 1
    return count


def _ending(reason: str, status: Sample, detail: str) -> tuple[Sample, str]:
    """What a `Finish` makes of the sample. **A tool call already seen outranks it** — the
    turn is not over, whatever reason the model gave for stopping this step.
    """
    if status is Sample.TOOL_CALL:
        return status, detail
    if reason == "stop":
        return Sample.OK, ""
    if reason == "length":
        return Sample.TRUNCATED, "cut off at max_tokens"
    return Sample.UNKNOWN, f"unread finish reason: {reason}"


def _malformed(replies: Sequence[Reply]) -> str:
    """The broken-marker count **over the whole case**, printed only when there is one.

    Summed across turns because a directive the parser refused on turn 1 is a protocol
    failure whether or not turn 3 went well — and only turn 3's numbers are printed
    otherwise. A `壊れ 0` on every line is noise; a `壊れ 3` on one line is the finding.
    """
    broken = sum(reply.malformed_markers for reply in replies)
    return f" (壊れ {broken})" if broken else ""


def repeated_ratio(text: str) -> float:
    """Share of character 5-grams that occur more than once. **Not a quality score.**

    It catches the one failure mode that is unambiguous on sight — the model saying the
    same clause twice — and says nothing about the ones that are not (stiffness, drift,
    translationese). Japanese repeats short spans naturally, so **only the trend across
    profiles means anything**; the absolute number does not.
    """
    grams = [text[i : i + 5] for i in range(len(text) - 4)]
    if not grams:
        return 0.0
    return round(1 - len(set(grams)) / len(grams), 3)


def profiles(
    model: str, overrides: Sequence[tuple[str, dict[str, object]]]
) -> list[tuple[str, LLMOptions]]:
    base = options_for(model, Purpose.CONVERSATION)
    return [(name, replace(base, **kwargs)) for name, kwargs in overrides]  # type: ignore[arg-type]


#: The throwaway turn. **Deliberately not one of `CASES`** — see `warm_up`.
WARM_UP_TURN = "ちょっと待っててね"

#: Ollama's context-slot count. **Read from the report, not from here** — the harness
#: cannot see a remote server's environment, so it states the precondition instead of
#: checking it (`warm_up`).
PARALLEL_ENV = "OLLAMA_NUM_PARALLEL"

#: `/api/show` and `/api/tags` are metadata reads; neither generates.
METADATA_TIMEOUT_S = 30.0

#: Wall-clock bound on one generation. **Not a `max_tokens` in disguise** — variant A has
#: to stay uncapped to be the pre-ADR-048 request, so what is bounded is the run, not the
#: reply. httpx's timeout does not do this: it fires on silence between reads, and a model
#: that streams forever is never silent.
#:
#: 180 s against replies measured at 30-120 tokens (`docs/measurements/phase2.md`) and a
#: whole extraction at 4.4 s. **It cannot fire on a generation anyone would want to keep**,
#: which is the only property it needs — a tighter number would start excluding samples for
#: being slow, and speed is one of the things the report compares.
GENERATION_DEADLINE_S = 180.0


def resolve_tag(model: str, listed: Sequence[Any]) -> tuple[str, str]:
    """The canonical tag `/api/tags` lists for `--model`, and its digest.

    ★ **`--model qwen3.5` is a name Ollama serves and `/api/tags` never lists.**
    `OllamaProvider._has_model` matches the tag-less form on purpose, and `is_qwen3()`
    accepts it too, so a run can perfectly well be made against `qwen3.5` — and an exact
    `name == model` lookup then finds nothing and records `(unknown)`, losing the one
    field that identifies the mutable thing the run used.

    So: the exact name, then `:latest` (what Ollama resolves a bare name to), then a
    unique match on the base name. **Ambiguity resolves to nothing rather than to a
    guess** — two tags sharing a base means the report cannot say which was served, and a
    digest naming the wrong weights is worse evidence than no digest at all.
    """
    by_name = {
        str(entry.get("name", "")): str(entry.get("digest", ""))
        for entry in listed
        if isinstance(entry, dict)
    }
    for candidate in (model, f"{model}:latest"):
        if candidate in by_name:
            return candidate, by_name[candidate]
    base = model.split(":", 1)[0]
    same_base = sorted(name for name in by_name if name.split(":", 1)[0] == base)
    if len(same_base) == 1:
        return same_base[0], by_name[same_base[0]]
    return "", ""


async def model_identity(
    model: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[str]:
    """What the selected tag **actually was, at the moment of the run.**

    ★ **A tag is mutable, and variant A is now defined by whatever it holds.** A publishes
    `temperature` and nothing else, so `top_p`, `top_k` and the penalties come from this
    model's own file — which is the honest way to reproduce the pre-ADR-048 request
    (`VARIANTS`) and, left at that, the way to lose the baseline. `qwen3.5:9b` re-pulled
    six months from now is a different set of numbers under the same name, and a report
    that only says "not sent" can no longer say what it compared against.

    So the run records it: the digest that identifies the weights, `/api/show`'s parameter
    block **verbatim** — copying it into a dict would be one more place to misread it —
    and **the engine version**. That last one is not padding: every field A leaves off
    that the model file does not set either falls through to *Ollama's* default, and those
    move between releases. `sampling.py` says as much about `repeat_penalty` ("Ollama
    0.33.2's own default is already 1.0 ... but a value this load-bearing is not left to a
    runtime's release notes"), and A is the variant made entirely of such fall-throughs.
    This is the same failure ADR-048 was written about, one level up: a file nobody read
    deciding the numbers.

    **A failure to read it does not stop the run**, because the comparison is still valid
    for the machine it ran on. It is printed as the caveat it is.

    `transport` is the hook for tests to substitute HTTP, as `OllamaProvider` has.
    **`None` when it is actually run.**
    """
    base = f"http://{HOST}:{DEFAULT_PORT}"
    try:
        async with httpx.AsyncClient(
            base_url=base, timeout=METADATA_TIMEOUT_S, transport=transport
        ) as http:
            shown = await http.post("/api/show", json={"model": model})
            shown.raise_for_status()
            tags = await http.get("/api/tags")
            tags.raise_for_status()
            version = await http.get("/api/version")
            version.raise_for_status()
            parameters = str(shown.json().get("parameters") or "").strip()
            listed = tags.json().get("models") or []
            engine = str(version.json().get("version") or "")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        return [
            f"- ⚠ **このタグが実際に何だったかを記録できなかった**（`{error}`）。"
            "**A の基準はこのレポートからは復元できない**——"
            "タグは書き換わるので、後から `/api/show` を引いても同じ値とは限らない",
            "",
        ]
    tag, digest = resolve_tag(model, listed)
    return [
        f"- model: `{tag or model}` / digest: `{digest or '(unknown)'}`"
        "（**タグは可変なので、A の基準を名指しできるのは digest である**）",
        f"- ollama: `{engine or '(unknown)'}`"
        "（**A が Modelfile にも無い欄で落ちる先はエンジンの既定値であり、版で動く**）",
        "",
        "<details><summary>`/api/show` の parameters（**A が継承した値そのもの**）</summary>",
        "",
        f"```text\n{parameters or '(empty — the model file sets nothing)'}\n```",
        "",
        "</details>",
        "",
    ]


async def warm_up(
    provider: LLMProvider, options: LLMOptions, tools: Sequence[ToolDescriptor]
) -> None:
    """One generation whose output is thrown away. **Run before every profile.**

    Profiles are compared in a fixed order against one loaded model, and the first request
    pays for a system-prompt KV cache the rest inherit — 644 ms cold against ~420 ms warm on
    this machine (`docs/measurements/phase1.md`). Left alone, that lands entirely on
    whichever variant is listed first and reads as if its settings caused it.

    **Once per run was not enough, and the reason is smaller but sharper.** Ollama reuses
    the longest common prefix of the previous request, so a warm-up that generated `CASES[0]`
    left that exact prompt — user message and all — cached for whichever variant ran next,
    while every later variant reached `CASES[0]` after the preceding variant's *last* case
    and shared only the system prefix. The gap is a handful of user tokens rather than the
    644/420 above, but it fell on exactly one printed cell and on the same variant every
    time. A neutral turn nobody measures, before each profile, leaves all five starting from
    the same cache state.

    It does not make the latency column an SLO measurement — that is `phase1.md`'s job, with
    a proper cold/warm split. It makes the column **comparable between the profiles below**,
    which is the only claim this file makes.

    ★ **And it makes that claim only on a single-slot server.** One request warms the one
    context slot it lands on. With `OLLAMA_NUM_PARALLEL` above 1, the other slots still
    hold whatever the *previous* variant left in them, so a later variant's case can be
    routed onto a longer cached prefix than variant A ever had — and the equalization this
    function exists for silently does not happen, leaving first-token latency correlated
    with variant order exactly as it was before. **The harness cannot check this**: the
    setting belongs to a server that may not be on this machine. So the report states the
    precondition instead of verifying it, and `PARALLEL_ENV` names it.
    """
    session = Session()
    reply = await generate(provider, session, replace(options, seed=0), WARM_UP_TURN, tools)
    if reply.status is Sample.FAILED:
        # ★ **A warm-up that did not generate did not warm anything**, and `generate()`
        # returns `Sample.FAILED` rather than raising — so left unread this is the one
        # failure in the run that is invisible. Every other one prints itself as `除外`;
        # this one would let the report claim a cache state it does not have, and the
        # profile's first case would pay a cost that reads as its settings.
        #
        # **`FAILED` only, because the question here is just "did inference run".** The
        # output was always going to be thrown away, so a warm-up that came back `EMPTY`
        # (the neutral turn answered with a marker and nothing else), `TOOL_CALL` or
        # `TRUNCATED` did its job: the server generated and the cache is warm. Ending a
        # ten-minute run over one of those would be discarding a warm cache for having
        # said the wrong thing with it.
        raise RuntimeError(f"warm-up failed: {reply.detail}")


#: The comparison. **A is what shipped**: `temperature` alone on the request, every other
#: field left off so the selected model's own file decides it.
#:
#: ★ **A is reproduced by omission, not by naming the values it inherited.** Spelling out
#: `qwen3.5:9b`'s file (`top_p 0.95 / top_k 20 / presence_penalty 1.5`) would make A a
#: baseline that only exists for that one tag — run against `qwen3:8b`, whose file may say
#: something else, it would be a request Lumi never sent, and every difference measured
#: against it would be a difference from a fabricated past. `is_qwen3()` gates the *family*
#: (B–E are the family's profile); it cannot gate one tag's Modelfile. Omission is exact
#: for whatever model is selected, and `_settings()` prints each omission as one.
VARIANTS: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "A (before: temperature only, rest from the model's own file)",
        {
            "temperature": 0.8,
            "top_p": None,
            "top_k": None,
            "min_p": None,
            "repeat_penalty": None,
            "presence_penalty": None,
            "frequency_penalty": None,
            "max_tokens": None,
        },
    ),
    ("B (Qwen card: presence_penalty 1.5)", {"presence_penalty": 1.5}),
    ("C (shipped profile: presence_penalty 0.0)", {}),
    ("D (C, presence_penalty 0.5)", {"presence_penalty": 0.5}),
    ("E (C, temperature 0.6)", {"temperature": 0.6}),
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--out", default="llm-profile-eval.md")
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    args = parser.parse_args()

    if not is_qwen3(args.model):
        # **Refusing beats reporting.** ADR-048 points at this harness as the way to decide
        # whether to switch models, and a run whose variant labels describe a different
        # family is evidence pointing the wrong way.
        parser.error(
            f"{args.model} has no variant table; VARIANTS is Qwen3-specific"
            " (see the module docstring)"
        )

    tools = exposed_tools()
    provider = OllamaProvider(args.model)
    await provider.load()

    lines = [
        f"# sampling profile A/B — `{args.model}`",
        "",
        f"- seed base: {args.seed_base}",
        *await model_identity(args.model),
        f"- tools: {', '.join(tool.name for tool in tools) or '(none)'}"
        "（`build_tool_registry()` の `list_exposed()`。本番と同じものをモデルに見せている）",
        "- **変種ごとに1回、捨てるための生成を回している**（KV キャッシュを揃えるため。"
        "ケースに含まれない中立の1ターン）。latency は**プロファイル間の比較にだけ**使える",
        f"- ⚠ **その「揃えた」が成り立つのは context slot が1つのときだけである。**"
        f"`{PARALLEL_ENV}` が 2 以上だと中立ターンは当たったスロット1つしか温めず、"
        "他のスロットには前の変種のケースのプロンプトが残る。"
        "後の変種のケースがその長い接頭辞に当たると、"
        "**揃えたはずの latency が変種の並び順と相関したままになる。**"
        f"harness は remote のサーバの環境を見られないので確認していない——"
        f"latency を読むなら `{PARALLEL_ENV}=1` で取り直すこと",
        "- **`marker` は parse できたマーカーの数で、ケース全体の合計である。**"
        "parse できなかったもの（壊れた JSON・`Emotion` に無い emotion・閉じ忘れ）は"
        "`壊れ N` として別に出す。**`MarkerStream` はそれを黙って落とす**ので、分けないと"
        "「マーカーを出さなかった」と「壊れたものを5回出した」が同じ 0 に見える。"
        "**マーカーの2列だけがケース合計なのは、プロトコル違反が"
        "どのターンで起きても違反だからである**（他の列は最終ターン1回の生成の性質）",
        "- **モデルの出力は引用ブロックに入れている。**"
        "閉じられていないコードフェンスが1つあるだけで、**以降のケースも変種も全部その中に入る**——"
        "壊れた出力は見えたまま、その1マスの中に留める",
        "- **`stop` で終わり、かつ発話テキスト が空でない text のみが sample である。**"
        " `length` / tool call / 失敗 / 空応答 / エンジンのエラー / "
        f"{GENERATION_DEADLINE_S:.0f} 秒の打ち切りは `除外` として印字し、比較には使わない。"
        "ケースは1ターンでも除外されたらそこで打ち切る"
        "（続きは別の会話になり、他の変種と比較できなくなるため）",
        "- **`<|ACT ...|>` は本番と同じ `MarkerStream` で取り除いてから数えている。**"
        "マーカーは ASCII 40字前後あり、残すと 字数 と 反復 が歪む。"
        "出た回数は `marker` 列に出す",
        "- **A は `temperature` 以外を一切送らない**（`max_tokens` を含む）。"
        "ADR-048 以前の要求そのものを再現しているためで、この非対称は意図的である。"
        "だからこそ打ち切られた sample を混ぜない",
        "- ★ **A の「送らない」欄を埋めるのは、上のモデル自身のファイルである。**"
        "値をここに書き写すと `qwen3.5:9b` 専用の基準になり、別のタグに対しては"
        "**Lumi が一度も送ったことのない要求**を基準に差を測ることになる。"
        "各欄が実際に何になったかは `/api/show` が答える",
        "",
    ]
    try:
        variants = profiles(args.model, VARIANTS)
        for label, options in variants:
            # **Before each profile, not once for the run.** Every variant then reaches its
            # first case from the same cache state (`warm_up`)
            await warm_up(provider, options, tools)
            lines += [f"## {label}", "", f"```\n{_settings(options)}\n```", ""]
            for index, case in enumerate(CASES):
                session = Session()
                seeded = replace(options, seed=args.seed_base + index)
                lines += _render(case, await _run_case(provider, session, seeded, case, tools))
            lines.append("")
    finally:
        await provider.unload()

    # Off the loop, because `ASYNC240` is right in general even where it is harmless here:
    # every request is already done by this point, so nothing is waiting on the write
    await asyncio.to_thread(Path(args.out).write_text, "\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}")


async def _run_case(
    provider: LLMProvider,
    session: Session,
    options: LLMOptions,
    case: Case,
    tools: Sequence[ToolDescriptor],
) -> list[Reply]:
    """One case, **stopping at the first turn that is not a reply.**

    Carrying on would generate turn 3 of a conversation whose turn 2 is missing or cut in
    half, and print it beside the same case from a variant that held the whole one.
    """
    replies: list[Reply] = []
    for turn in case.turns:
        reply = await generate(provider, session, options, turn, tools)
        replies.append(reply)
        if reply.status is not Sample.OK:
            break
    return replies


def _settings(options: LLMOptions) -> str:
    """Every decoding field, **including the ones left to the model file.**

    Printing only what is set would hide the difference ADR-048 is about: `None` is not a
    neutral value, it is `qwen3.5:9b`'s Modelfile deciding (`presence_penalty 1.5`). A
    settings block that says nothing about a field reads as "this profile does not use it".
    """
    # `fields()`, not `vars()` — `LLMOptions` is `slots=True` and has no `__dict__`
    return "\n".join(
        f"{field.name} = {_value(getattr(options, field.name))}"
        for field in fields(options)
        if field.name not in ("model", "seed")
    )


def _value(value: object) -> str:
    return "(not sent → the model file decides)" if value is None else str(value)


def _render(case: Case, replies: Sequence[Reply]) -> list[str]:
    """One case's block.

    **The measurement columns describe the last turn; the marker columns describe the
    case.** That split is not tidiness — the two answer different questions. Latency,
    length and repetition are properties of one generation, and for the multi-turn case
    the last one is the point (drift shows up on turn 3). Protocol adherence is not: a
    broken directive on turn 1 is a broken directive, and reporting only turn 3 would let
    a profile that mangled two of them read as clean.
    """
    last = replies[-1]
    lines = [f"### {case.name} — 期待: {case.expectation}", ""]
    if last.status is Sample.OK:
        lines += [
            f"- first token {last.first_token_ms} ms / total {last.total_ms} ms "
            f"/ {last.tokens} tok / {len(last.text)} 字 / 反復 {repeated_ratio(last.text)}"
            f" / marker {sum(reply.intents for reply in replies)}{_malformed(replies)}"
            f" / prompt {last.prompt_tokens} tok",
            "",
        ]
    else:
        # **No numbers at all for an excluded sample.** Printing them greyed out is how
        # they end up in a comparison anyway
        lines += [
            f"- **除外（{last.status.value}）**: {last.detail}"
            f" — 比較に使わない（turn {len(replies)}/{len(case.turns)} で打ち切り）",
            "",
        ]
    for turn, reply in zip(case.turns, replies, strict=False):
        lines += [
            quoted(f"**user:** {turn}"),
            ">",
            quoted(f"**lumi:** {reply.text or '*(空)*'}"),
            "",
        ]
    return lines


def quoted(text: str) -> str:
    """A block quote, **because a reply is model output and this file is Markdown.**

    ★ The `technical` case asks about `git rebase`, so a reply containing an opening code
    fence is an ordinary Tuesday — and one that never closes it (a truncated technical
    answer will do) swallows **the rest of the report**: every later case, every later
    variant, rendered inside that block. The experiment would be unreadable because the
    model wrote three backticks.

    Quoting bounds it. A fence inside a block quote is closed by the end of the quote, and
    the blank line after this ends it — so malformed output stays visible, and stays
    inside its own cell. It also stops a reply's `###` from becoming a section of the
    report and a `---` from cutting one.
    """
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines() or [""])


def fenced(text: str, info: str = "text") -> str:
    """A code block sized to its contents. **For text a server wrote, not Lumi.**

    `/api/show` returns whatever the Modelfile holds, and three backticks in it would end
    the block early and spill the rest into the report. A fence only closes on a run of
    backticks at least as long as itself, so this one is always longer than anything
    inside.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text}\n{fence}"


if __name__ == "__main__":
    asyncio.run(main())
