"""Ollama。**Lumi は起動もインストールもしない**（ADR-023）。

決定 → ADR-023 / 状態 → docs/architecture/setup.md §2b

## 誰が何を判定するか

| 判定 | 誰が | 結果 |
|---|---|---|
| Ollama が**入っているか** | `lumi.setup.detect`（実行ファイルを探す） | `not_configured` |
| Ollama が**動いているか** | ここ（HTTP） | `ProviderUnavailable` |
| モデルが**あるか** | ここ（`/api/tags`） | `ProviderNotConfigured("model_missing")` |

Provider は HTTP しか見ないので「入っていない」と「起動していない」を区別できない。
**区別は setup 側が持つ**（→ ユーザーへの案内が変わる）。

## 宛先は 127.0.0.1 に固定する。ポートだけ設定できる

TTS（`aivisspeech.py`）と同じ理由である。**ホストを差し替えられるようにすると、
「Lumi が任意のサーバに会話内容を送る機能」になる。**

ADR-023 は「検出できないケースは設定で明示的に指定させる」としているが、
それで足りるのは**ポート**までである。リモートの推論サーバを使いたい場合は
ホストを可変にするのではなく、**別の `LLMProvider` を足す**
（クラウド LLM と同じ枠組み。DESIGN.md §1 の Network-optional）。
そうすれば「外部へ送っている」ことが Provider の選択として画面に出る。
"""

from __future__ import annotations

import json
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
from lumi.tools.base import ToolDescriptor

log = lumi_logging.get_logger(__name__)

#: **設定にしない。** 変えたいなら Provider ごと差し替える
HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 11434

#: 生存確認。**長く待たない**（起動していないことを素早く確定させる）
PROBE_TIMEOUT_S: Final = 2.0
#: ストリームの待ち時間。初トークンまでの SLO は 0.28s だが、
#: 初回のモデルロードは数十秒かかる。**無限には待たない**
STREAM_TIMEOUT_S: Final = 120.0


class OllamaProvider:
    """`LLMProvider` の実装。"""

    kind = ProviderKind.LLM

    __slots__ = ("_base", "_loaded", "_model", "_transport", "id")

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
        #: テストから HTTP を差し替えるための窓口。**本番では None**
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    # ── ライフサイクル ────────────────────────────────────

    async def load(self) -> None:
        """**冪等。** 繋がることとモデルがあることを確かめるだけ（重みは Ollama が持つ）。"""
        if self._loaded:
            return

        version = await self._version()
        if version is None:
            raise ProviderUnavailable(
                "ollama_not_running", f"{self._base} に応答が無い。Ollama を起動してください"
            )

        models = await self._models()
        if not self._has_model(models):
            # **「入っていない」ではなく「モデルが無い」。** 案内すべき行動が違う
            raise ProviderNotConfigured(
                "model_missing", f"{self._model} が見つからない。`ollama pull {self._model}`"
            )

        self._loaded = True
        log.info("llm.loaded", provider=self.id, version=version)

    async def unload(self) -> None:
        """**Ollama のプロセスには触らない**（ADR-023）。手放すのは Lumi 側の状態だけ。"""
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def resource_hint(self) -> ResourceHint:
        """**別プロセスなので Lumi の VRAM 予算の外にある。**

        実際には Ollama が GPU を使うが、それを Lumi の
        `ModelResourceManager`（Phase 5）が管理することはできない。
        `vram_estimate_mb=0` は「使わない」ではなく「**Lumi は数えない**」の意味である。
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

    # ── 推論 ──────────────────────────────────────────────

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
            "options": {"temperature": options.temperature},
        }
        if options.max_tokens is not None:
            payload["options"]["num_predict"] = options.max_tokens
        if options.think:
            payload["think"] = True
        if tools:
            payload["tools"] = [_tool_payload(t) for t in tools]

        try:
            async with (
                self._client(STREAM_TIMEOUT_S) as client,
                client.stream("POST", f"{self._base}/api/chat", json=payload) as response,
            ):
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise ProviderUnavailable("ollama_http_error", f"{response.status_code} {body}")

                async for line in response.aiter_lines():
                    if cancel_token.is_set:
                        # **cooperative。** ここで抜けると `with` が接続を閉じる
                        log.info("llm.cancelled", reason=cancel_token.reason)
                        return
                    if not line.strip():
                        continue
                    for event in _parse_line(line):
                        yield event
                        if isinstance(event, Finish):
                            return
        except httpx.HTTPError as error:
            # ストリームが始まったあとの切断も含む。**黙って終わらせない**
            yield LLMFailure(message=str(error))

    # ── 内部 ──────────────────────────────────────────────

    async def _version(self) -> str | None:
        """起動していなければ `None`（**例外にしない**。ポーリングに使うため）。"""
        try:
            async with self._client(PROBE_TIMEOUT_S) as client:
                response = await client.get(f"{self._base}/api/version")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return str(data.get("version", "")) if isinstance(data, dict) else None

    async def _models(self) -> list[str]:
        try:
            async with self._client(PROBE_TIMEOUT_S) as client:
                response = await client.get(f"{self._base}/api/tags")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ProviderUnavailable("ollama_tags_failed", str(error)) from error

        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []
        return [str(m.get("name", "")) for m in models if isinstance(m, dict)]

    def _has_model(self, models: Sequence[str]) -> bool:
        """`qwen3:8b` と `qwen3:8b` / タグ省略（`qwen3`）の両方を拾う。"""
        wanted = self._model
        for name in models:
            if name == wanted or name.split(":")[0] == wanted.split(":")[0]:
                return True
        return False


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
    """NDJSON の1行を 0個以上のイベントにする。

    **壊れた行で落ちない。** 1行読めなかっただけで会話全体を失う方が悪い。
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
                reason=str(chunk.get("done_reason", "stop")),
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
            # 実装によっては JSON 文字列で来る。**読めなければ捨てる**（推測で埋めない）
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
