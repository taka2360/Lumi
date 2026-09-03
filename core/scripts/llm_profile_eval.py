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

`Finish(reason="stop")`, text only. Everything else is **excluded from the comparison and
printed as excluded**, because every other ending measures something other than the
profile:

| ending | why it is not a sample |
|---|---|
| `length` | the reply stopped where `max_tokens` was, not where the model was done. |
| | Variant A sets no cap at all, so a capped variant would read as "shorter and |
| | faster" for a reason that is not its sampling |
| a tool call | production would run it and generate again; the text and the latency |
| | here are one step of a turn, not a turn |
| `LLMFailure` / no `Finish` | there is no reply to measure |
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

**It does not run on a model family it has no variants for.** `VARIANTS` names Qwen's own
numbers and spells out the Modelfile values `qwen3.5:9b` ships with; pointed at `gemma3:12b`
those labels would be lies about a base that `options_for()` had already fallen back to
temperature-only. A new family needs its own variant table, not a `--model` flag.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

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
from lumi.providers.llm.base import Finish, LLMFailure, LLMOptions, TextDelta, ToolCall
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
    Case("vague", ("うーん、どうしよ",), "文脈を踏まえる。一般論を始めない"),
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
    #: The model called a tool. Production would run it and generate again
    TOOL_CALL = "tool_call"
    #: `LLMFailure`, or a stream that ended without a `Finish`
    FAILED = "failed"
    #: A `Finish.reason` this script has not read. **Never assumed to be whole**
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reply:
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
    provider: OllamaProvider,
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
    parts: list[str] = []
    tokens = 0
    prompt_tokens = 0
    # **Starts invalid.** A stream that ends without a `Finish` has not said it is done,
    # and the joined text of one is indistinguishable from a whole short answer
    status = Sample.FAILED
    detail = "the stream ended without a Finish"
    async for event in provider.stream(prompt.messages, tools, options, CancelToken()):
        if isinstance(event, TextDelta):
            if first is None:
                first = time.perf_counter()
            parts.append(event.text)
        elif isinstance(event, ToolCall):
            # Recorded, not run. **Latency and length here describe one step of a turn**
            status, detail = Sample.TOOL_CALL, f"the model called {event.name}"
        elif isinstance(event, Finish):
            tokens = event.usage.get("completion_tokens", 0)
            prompt_tokens = event.usage.get("prompt_tokens", 0)
            status, detail = _ending(event.reason, status, detail)
        elif isinstance(event, LLMFailure):
            status, detail = Sample.FAILED, event.message
    ended = time.perf_counter()

    reply = "".join(parts)
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
    )


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


async def warm_up(
    provider: OllamaProvider, options: LLMOptions, tools: Sequence[ToolDescriptor]
) -> None:
    """One generation whose output is thrown away. **So that A is not the only cold run.**

    Profiles are compared in a fixed order against one loaded model, and the first request
    pays for a KV cache the rest inherit — 644 ms cold against ~420 ms warm on this machine
    (`docs/measurements/phase1.md`). Left alone, that difference lands entirely on whichever
    variant happens to be listed first and reads as if its settings caused it.

    It does not make the latency column an SLO measurement — that is `phase1.md`'s job, with
    a proper cold/warm split. It makes the column **comparable between the profiles below**,
    which is the only claim this file makes.
    """
    session = Session()
    await generate(provider, session, replace(options, seed=0), CASES[0].turns[0], tools)


#: The comparison. **A is what shipped**: `temperature` alone, every other value inherited
#: from the model file (`top_p 0.95 / top_k 20 / presence_penalty 1.5`), which is why it is
#: spelled out here rather than written as "the defaults".
VARIANTS: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "A (before: temperature only, rest from the Modelfile)",
        {
            "temperature": 0.8,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": None,
            "repeat_penalty": None,
            "presence_penalty": 1.5,
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
        f"- tools: {', '.join(tool.name for tool in tools) or '(none)'}"
        "（`build_tool_registry()` の `list_exposed()`。本番と同じものをモデルに見せている）",
        "- 最初に1回、捨てるための生成を回している（KV キャッシュを揃えるため）。"
        "latency は**プロファイル間の比較にだけ**使える",
        "- **`stop` で終わった text のみが sample である。** `length` / tool call / 失敗は"
        "`除外` として印字し、比較には使わない。ケースは1ターンでも除外されたらそこで打ち切る"
        "（続きは別の会話になり、他の変種と比較できなくなるため）",
        "- **A だけ `max_tokens` を送らない。** 出荷前の要求そのものを再現しているためで、"
        "この非対称は意図的である。だからこそ打ち切られた sample を混ぜない",
        "",
    ]
    try:
        variants = profiles(args.model, VARIANTS)
        await warm_up(provider, variants[0][1], tools)
        for label, options in variants:
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
    provider: OllamaProvider,
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
    last = replies[-1]
    lines = [f"### {case.name} — 期待: {case.expectation}", ""]
    if last.status is Sample.OK:
        lines += [
            f"- first token {last.first_token_ms} ms / total {last.total_ms} ms "
            f"/ {last.tokens} tok / {len(last.text)} 字 / 反復 {repeated_ratio(last.text)}"
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
        lines += [f"> {turn}", "", reply.text or "*(空)*", ""]
    return lines


if __name__ == "__main__":
    asyncio.run(main())
