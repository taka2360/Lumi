"""Ollama. **Lumi neither starts nor installs it** (ADR-023).

Decision → ADR-023 / State → docs/architecture/setup.md §2b

## Who decides what

| Check | Who | Result |
|---|---|---|
| Is Ollama **installed** | `lumi.setup.detect` (looks for the executable) | `not_configured` |
| Is Ollama **running** | here (HTTP) | `ProviderUnavailable` |
| Does the model **exist** | here (`/api/tags`) | `ProviderNotConfigured("model_missing")` |

The Provider only sees HTTP, so it can't distinguish "not installed" from "not running."
**That distinction belongs to the setup side** (→ it changes the guidance shown to the user).

## The destination is pinned to 127.0.0.1. Only the port is configurable

Same reason as TTS (`aivisspeech.py`). **Making the host swappable would turn this into
"a feature where Lumi sends conversation content to an arbitrary server."**

ADR-023 says "cases that can't be auto-detected should be set explicitly in config,"
but that only covers the **port**. If a remote inference server is wanted, the fix
isn't making the host variable — it's **adding a separate `LLMProvider`**
(the same framework as a cloud LLM. Network-optional from DESIGN.md §1).
That way "sending to the outside" shows up on screen as a Provider choice.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Final

import httpx

from lumi import logging as lumi_logging
from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderKind,
    ProviderNotConfigured,
    ProviderUnavailable,
    ResourceHint,
    UnloadPolicy,
)
from lumi.providers.llm.base import (
    Finish,
    LLMEvent,
    LLMFailure,
    LLMOptions,
    Message,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)
from lumi.providers.llm.endpoint import DEFAULT_PORT, HOST
from lumi.tools.base import ToolDescriptor

log = lumi_logging.get_logger(__name__)

#: Liveness probe. **Don't wait long** (quickly confirm it isn't running)
PROBE_TIMEOUT_S: Final = 2.0
#: Stream wait time. The SLO for first token is 0.28s, but the first model load can
#: take tens of seconds. **Never wait forever**
STREAM_TIMEOUT_S: Final = 120.0
#: Putting the weights in memory. Reading tens of GB off a cold disk is minutes on the first
#: run, and **this happens at startup where nobody is waiting on a reply**
LOAD_TIMEOUT_S: Final = 600.0

#: How long Ollama keeps the weights resident after the last request. **`-1` = for as long as
#: Lumi runs**, which is what `UnloadPolicy.PINNED` below already claims.
#:
#: Ollama's own default is 5 minutes. **A desktop character idles far longer than that** — every
#: gap over 5 minutes was silently paying a 3767 ms reload on the next reply (measured
#: 2026-08-18), which is the "why is the first answer so slow" the user actually feels.
#:
#: **Released in `unload()`.** Lumi asked for the residency, so Lumi gives it back. An unclean
#: exit leaves it resident until Ollama is restarted — accepted, because the alternative is
#: making the user wait 4 seconds every time they come back from lunch.
KEEP_ALIVE: Final = -1


class OllamaProvider:
    """Implementation of `LLMProvider`."""

    kind = ProviderKind.LLM

    __slots__ = ("_base", "_http", "_loaded", "_model", "_transport", "id")

    def __init__(
        self,
        model: str,
        *,
        port: int = DEFAULT_PORT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.id = f"ollama:{model}"
        self._model = model
        self._base = f"http://{HOST}:{port}"
        self._loaded = False
        #: Hook for tests to substitute HTTP. **`None` in production**
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    def _session(self) -> httpx.AsyncClient:
        """**One client, reused.** Created lazily so that importing the module isn't already
        preparing to talk to the network (docs/architecture/setup.md §1 Principle 1).

        **Constructing an `AsyncClient` costs ~0.2 s** (SSL context + proxy environment), and
        with one per request that lands inside `llm_first_token_ms` on **every** turn:
        measured 569 ms → 411 ms just by reusing it (2026-08-18).

        `aivisspeech.py` already says this in its own docstring — "a client per request pays
        connection setup on every sentence, and that cost lands on `tts_first_audio_ms`".
        **This module was doing exactly what that warning describes**, on the span with the
        tighter budget.
        """
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=STREAM_TIMEOUT_S, transport=self._transport)
        return self._http

    # ── Lifecycle ────────────────────────────────────

    async def load(self) -> None:
        """**Idempotent.** Confirms connectivity, that the model exists, **and puts the weights
        in memory.**

        Confirming existence is not loading. `/api/tags` answers in milliseconds while the
        weights are still on disk, so a `load()` that stopped there was **handing the first
        reply a 3767 ms bill** (measured 2026-08-18). "The weights live in Ollama, so it isn't
        Lumi's problem" doesn't survive contact with the user: the wait is the same either way,
        and **which process allocated the memory is invisible to them.**

        -> docs/interfaces/provider.md "`load()` is not a connection check"
        """
        if self._loaded:
            return

        version = await self._version()
        if version is None:
            raise ProviderUnavailable(
                "ollama_not_running", f"No response from {self._base}. Please start Ollama"
            )

        models = await self._models()
        if not self._has_model(models):
            # **Not "not installed" — "the model is missing."** The action to guide the user toward
            # differs
            raise ProviderNotConfigured(
                "model_missing", f"Model {self._model} not found (`ollama pull {self._model}`)"
            )

        await self._preload()
        self._loaded = True
        log.info("llm.loaded", provider=self.id, version=version)

    async def unload(self) -> None:
        """**Never touches the Ollama process** (ADR-023). Only releases Lumi's own state —
        **including the residency Lumi itself asked for.**

        Skipping the release would leave several GB pinned after Lumi exits, which is not
        Lumi's memory to keep.
        """
        self._loaded = False
        await self._release()
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    def is_loaded(self) -> bool:
        return self._loaded

    def resource_hint(self) -> ResourceHint:
        """**A separate process, so it's outside Lumi's VRAM budget.**

        Ollama does use the GPU in practice, but Lumi's `ModelResourceManager`
        (Phase 5) has no way to manage it. `vram_estimate_mb=0` means
        "**Lumi doesn't count it**," not "it uses none."
        """
        return ResourceHint(
            device_pref=DevicePref.EXTERNAL_PROCESS,
            vram_estimate_mb=0,
            load_time_estimate_ms=0,
            unload_policy=UnloadPolicy.PINNED,
        )

    def attribution(self) -> Attribution:
        return Attribution(
            display_name="Ollama",
            credit_text=f"LLM: Ollama ({self._model})",
            license_name="MIT",
            license_url="https://github.com/ollama/ollama/blob/main/LICENSE",
            homepage_url="https://ollama.com",
        )

    # ── Inference ──────────────────────────────────────────────

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDescriptor] | None,
        options: LLMOptions,
        cancel_token: CancelToken,
    ) -> AsyncIterator[LLMEvent]:
        payload: dict[str, Any] = {
            "model": options.model or self._model,
            "messages": [_message_payload(m) for m in messages],
            "stream": True,
            "options": _sampling_payload(options),
            # **Sent on every request, not just the preload.** Ollama restarts the countdown
            # from the last call, so omitting it here would silently fall back to the 5-minute
            # default and evict the model between conversations
            "keep_alive": KEEP_ALIVE,
        }
        # ★ **Always sent, including `False`.** Hybrid-reasoning models (Qwen3.5, ...) think by
        # default, and omitting the field leaves that default in place — so `think=False` would
        # be silently ignored. Measured 2026-08-16: thinking pushed time-to-first-spoken-token
        # from 272 ms to 5578 ms, which is the entire p50 budget spent before Lumi says a word
        # (docs/measurements/phase1.md).
        payload["think"] = options.think
        if tools:
            payload["tools"] = [_tool_payload(t) for t in tools]

        try:
            async with self._session().stream(
                "POST", f"{self._base}/api/chat", json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderUnavailable("ollama_http_error", f"{response.status_code} {body}")

                async for line in response.aiter_lines():
                    if cancel_token.is_set:
                        # **cooperative.** Breaking out here lets the `with` block close the
                        # connection
                        log.info("llm.cancelled", reason=cancel_token.reason)
                        return
                    if not line.strip():
                        continue
                    for event in _parse_line(line):
                        yield event
                        if isinstance(event, Finish):
                            return
        except httpx.HTTPError as error:
            # Includes disconnects after the stream has started. **Never let it end silently**
            yield LLMFailure(message=str(error))

    # ── Internal ──────────────────────────────────────────────

    async def _preload(self) -> None:
        """Puts the weights in memory **now**, so the first reply doesn't.

        `messages: []` is Ollama's own way to load a model without generating anything.

        **A failure here is not a failure to load.** `/api/tags` already said the model
        exists, so the worst case is that the first turn pays what it used to pay — slow, not
        broken. **It still says so out loud** rather than looking like a warm start.
        """
        started = time.perf_counter()
        try:
            response = await self._session().post(
                f"{self._base}/api/chat",
                json={"model": self._model, "messages": [], "keep_alive": KEEP_ALIVE},
                timeout=LOAD_TIMEOUT_S,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            log.warning("llm.preload_failed", provider=self.id, detail=str(error))
            return
        log.info(
            "llm.preloaded", provider=self.id, ms=round((time.perf_counter() - started) * 1000)
        )

    async def _release(self) -> None:
        """Gives the residency back (`keep_alive: 0`). **Best effort** — Lumi is shutting down,
        and failing to reach a service that may already be gone is not news.
        """
        if self._http is None or self._http.is_closed:
            return
        try:
            await self._http.post(
                f"{self._base}/api/chat",
                json={"model": self._model, "messages": [], "keep_alive": 0},
                timeout=PROBE_TIMEOUT_S,
            )
        except httpx.HTTPError as error:
            log.info("llm.release_failed", provider=self.id, detail=str(error))

    async def _version(self) -> str | None:
        """`None` if not running (**not raised as an exception** — used for polling)."""
        try:
            response = await self._session().get(
                f"{self._base}/api/version", timeout=PROBE_TIMEOUT_S
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return str(data.get("version", "")) if isinstance(data, dict) else None

    async def _models(self) -> list[str]:
        try:
            response = await self._session().get(f"{self._base}/api/tags", timeout=PROBE_TIMEOUT_S)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ProviderUnavailable("ollama_tags_failed", str(error)) from error

        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []
        return [str(m.get("name", "")) for m in models if isinstance(m, dict)]

    def _has_model(self, models: Sequence[str]) -> bool:
        """Matches both an exact `qwen3:8b` and the tag-less form (`qwen3`)."""
        wanted = self._model
        for name in models:
            if name == wanted or name.split(":")[0] == wanted.split(":")[0]:
                return True
        return False


#: `LLMOptions` field → the name Ollama's `options` block uses. **Everything Lumi decides
#: about decoding goes through this table**, so "which knobs does Lumi actually turn" is one
#: list rather than a scan of the request builder.
_OPTION_NAMES: Final = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "min_p": "min_p",
    "repeat_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
    "max_tokens": "num_predict",
    "seed": "seed",
}


def _sampling_payload(options: LLMOptions) -> dict[str, Any]:
    """The `options` block. **A `None` field is omitted, and omission is inheritance.**

    Ollama fills anything left out from the model's Modelfile — `qwen3.5:9b` carries
    `temperature 1 / top_k 20 / top_p 0.95 / presence_penalty 1.5` there. So this function
    is not "send what was set"; it is **the exact list of decisions Lumi is taking away
    from the model file** (ADR-048), and the profiles in `llm/sampling.py` are what decide
    how long that list is.
    """
    payload: dict[str, Any] = {}
    for field, name in _OPTION_NAMES.items():
        value = getattr(options, field)
        if value is not None:
            payload[name] = value
    return payload


def _message_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.name:
        payload["name"] = message.name
    return payload


def _tool_payload(tool: ToolDescriptor) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _parse_line(line: str) -> list[LLMEvent]:
    """Turns one line of NDJSON into zero or more events.

    **Never crash on a malformed line.** Losing the whole conversation over one
    unreadable line would be worse.
    """
    try:
        chunk = json.loads(line)
    except json.JSONDecodeError:
        log.warning("llm.bad_chunk", line=line[:200])
        return []
    if not isinstance(chunk, dict):
        return []

    events: list[LLMEvent] = []
    message = chunk.get("message")
    if isinstance(message, dict):
        thinking = message.get("thinking")
        if isinstance(thinking, str) and thinking:
            events.append(ReasoningDelta(text=thinking))

        content = message.get("content")
        if isinstance(content, str) and content:
            events.append(TextDelta(text=content))

        events.extend(_tool_calls(message.get("tool_calls")))

    if chunk.get("done"):
        events.append(
            Finish(
                # `or`, not a default: **a `null` `done_reason` is an absent one.**
                # `chunk.get(k, "stop")` returns `None` when the key is present and null,
                # and `str(None)` is the literal `"None"` — a reason no reader recognises,
                # handed to every caller that branches on how the generation ended.
                reason=str(chunk.get("done_reason") or "stop"),
                usage={
                    "prompt_tokens": int(chunk.get("prompt_eval_count", 0) or 0),
                    "completion_tokens": int(chunk.get("eval_count", 0) or 0),
                },
            )
        )
    return events


def _tool_calls(raw: Any) -> list[LLMEvent]:
    if not isinstance(raw, list):
        return []
    calls: list[LLMEvent] = []
    for index, item in enumerate(raw):
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            # Some implementations send this as a JSON string. **Discard if unreadable** (never fill
            # in a guess)
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                log.warning("llm.bad_tool_arguments", name=function.get("name"))
                continue
        if not isinstance(arguments, Mapping):
            continue
        calls.append(
            ToolCall(
                id=str(item.get("id") or f"call_{index}"),
                name=str(function.get("name", "")),
                arguments=dict(arguments),
            )
        )
    return calls
