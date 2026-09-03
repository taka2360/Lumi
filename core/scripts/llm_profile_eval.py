"""A/B harness for sampling profiles. **Dev-time only; never imported by Core.**

    uv run python scripts/llm_profile_eval.py --out ../eval.md

Why a script and not a test: `.claude/rules/python-core.md` says Core must be testable
without calling an LLM. **This calls one on purpose** — the question it answers ("does
Japanese come out better") has no assertion, only output a person reads.

## What it holds fixed

Everything except the profile. The same five cases, the same seed per case, the **real**
`assemble()` over the **real** Content Pack persona and `SPEECH_PROTOCOL` — a persona-drift
comparison run against a hand-written prompt would be measuring a prompt that is not the
one Lumi ships.

`seed` is what makes the comparison a comparison: without it, two profiles differ by
sampling noise as much as by their settings, and five cases is nowhere near enough to
average that out. **Production never sets one** (`LLMOptions.seed`).

## What it does not do

**It does not score.** Naturalness of Japanese is not a number this repo can compute, and
a made-up scorer would launder taste into evidence. What is counted is only what counting
answers honestly: length, latency, and how much of the reply is literally repeated text.

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
from pathlib import Path

from lumi.agent.prompt import assemble
from lumi.agent.session import Session
from lumi.content.pack import load_character
from lumi.kernel.cancellation import CancelToken
from lumi.provenance import TrustLevel
from lumi.providers.llm.base import Finish, LLMFailure, LLMOptions, TextDelta
from lumi.providers.llm.ollama import OllamaProvider
from lumi.providers.llm.sampling import Purpose, is_qwen3, options_for

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


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    first_token_ms: int
    total_ms: int
    #: Reported by Ollama. `0` when the stream ended without a `Finish`
    tokens: int
    #: What the assembled prompt actually cost. **Not the estimate** `agent/prompt.py` budgets
    #: against — the point of printing it is to see how far apart the two are
    prompt_tokens: int


async def generate(
    provider: OllamaProvider, session: Session, options: LLMOptions, text: str
) -> Reply:
    """One turn, taking the same path a real turn takes: `assemble()` then `stream()`."""
    session.record_user_utterance(text)
    prompt = assemble(persona=load_character(PACK).persona, session=session)

    started = time.perf_counter()
    first: float | None = None
    parts: list[str] = []
    tokens = 0
    prompt_tokens = 0
    async for event in provider.stream(prompt.messages, None, options, CancelToken()):
        if isinstance(event, TextDelta):
            if first is None:
                first = time.perf_counter()
            parts.append(event.text)
        elif isinstance(event, Finish):
            tokens = event.usage.get("completion_tokens", 0)
            prompt_tokens = event.usage.get("prompt_tokens", 0)
        elif isinstance(event, LLMFailure):
            parts.append(f"[FAILURE: {event.message}]")
    ended = time.perf_counter()

    reply = "".join(parts)
    session.record_lumi_turn(reply, TrustLevel.TRUSTED)
    return Reply(
        text=reply,
        first_token_ms=round(((first or ended) - started) * 1000),
        total_ms=round((ended - started) * 1000),
        tokens=tokens,
        prompt_tokens=prompt_tokens,
    )


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


async def warm_up(provider: OllamaProvider, options: LLMOptions) -> None:
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
    await generate(provider, session, replace(options, seed=0), CASES[0].turns[0])


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

    provider = OllamaProvider(args.model)
    await provider.load()

    lines = [
        f"# sampling profile A/B — `{args.model}`",
        "",
        f"- seed base: {args.seed_base}",
        "- 最初に1回、捨てるための生成を回している（KV キャッシュを揃えるため）。"
        "latency は**プロファイル間の比較にだけ**使える",
        "",
    ]
    try:
        variants = profiles(args.model, VARIANTS)
        await warm_up(provider, variants[0][1])
        for label, options in variants:
            lines += [f"## {label}", "", f"```\n{_settings(options)}\n```", ""]
            for index, case in enumerate(CASES):
                session = Session()
                seeded = replace(options, seed=args.seed_base + index)
                replies = [await generate(provider, session, seeded, turn) for turn in case.turns]
                lines += _render(case, replies)
            lines.append("")
    finally:
        await provider.unload()

    # Off the loop, because `ASYNC240` is right in general even where it is harmless here:
    # every request is already done by this point, so nothing is waiting on the write
    await asyncio.to_thread(Path(args.out).write_text, "\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}")


def _settings(options: LLMOptions) -> str:
    # `fields()`, not `vars()` — `LLMOptions` is `slots=True` and has no `__dict__`
    return "\n".join(
        f"{field.name} = {getattr(options, field.name)}"
        for field in fields(options)
        if field.name not in ("model", "seed") and getattr(options, field.name) is not None
    )


def _render(case: Case, replies: Sequence[Reply]) -> list[str]:
    last = replies[-1]
    lines = [
        f"### {case.name} — 期待: {case.expectation}",
        "",
        f"- first token {last.first_token_ms} ms / total {last.total_ms} ms "
        f"/ {last.tokens} tok / {len(last.text)} 字 / 反復 {repeated_ratio(last.text)}"
        f" / prompt {last.prompt_tokens} tok",
        "",
    ]
    for turn, reply in zip(case.turns, replies, strict=True):
        lines += [f"> {turn}", "", reply.text or "*(空)*", ""]
    return lines


if __name__ == "__main__":
    asyncio.run(main())
