"""Consent-gated Ollama model acquisition through the fixed local API.

Decision → ADR-037. Ollama itself is never fetched or started here. The only HTTP
destination is 127.0.0.1; Ollama owns the external model download after the user has
approved a pinned model and its displayed size.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import httpx

from lumi.providers.llm.ollama import DEFAULT_PORT, HOST

PULL_TIMEOUT_S: Final = 60.0 * 60.0


@dataclass(frozen=True, slots=True)
class OllamaModelArtifact:
    name: str
    display_name: str
    size_bytes: int

    def to_payload(self) -> dict[str, object]:
        return {
            "model": self.name,
            "display_name": self.display_name,
            "size_bytes": self.size_bytes,
        }


QWEN_35_9B = OllamaModelArtifact("qwen3.5:9b", "Qwen 3.5 9B", 6_600_000_000)
QWEN_35_4B = OllamaModelArtifact("qwen3.5:4b", "Qwen 3.5 4B", 3_400_000_000)
OLLAMA_MODELS: Final[dict[str, OllamaModelArtifact]] = {
    artifact.name: artifact for artifact in (QWEN_35_9B, QWEN_35_4B)
}


class OllamaPullError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


PullProgress = Callable[[int, int], Awaitable[None]]


async def pull_ollama_model(
    artifact: OllamaModelArtifact,
    *,
    progress: PullProgress | None = None,
    port: int = DEFAULT_PORT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Pulls one allowlisted model after consent, reporting Ollama's byte progress."""
    if OLLAMA_MODELS.get(artifact.name) != artifact:
        raise OllamaPullError("unknown_model", artifact.name)

    try:
        async with httpx.AsyncClient(
            timeout=PULL_TIMEOUT_S,
            transport=transport,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST",
                f"http://{HOST}:{port}/api/pull",
                json={"model": artifact.name, "stream": True},
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise OllamaPullError(
                        "ollama_pull_http_error", f"{response.status_code} {body}"
                    )

                succeeded = False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError as error:
                        raise OllamaPullError("ollama_pull_invalid_response", str(error)) from error
                    if not isinstance(event, dict):
                        raise OllamaPullError("ollama_pull_invalid_response", line)
                    if event.get("error"):
                        raise OllamaPullError("ollama_pull_failed", str(event["error"]))
                    completed = event.get("completed")
                    total = event.get("total")
                    if (
                        progress is not None
                        and isinstance(completed, int)
                        and isinstance(total, int)
                        and total > 0
                    ):
                        await progress(completed, total)
                    if event.get("status") == "success":
                        succeeded = True

                if not succeeded:
                    raise OllamaPullError(
                        "ollama_pull_incomplete", "Ollama closed the stream before success"
                    )
    except OllamaPullError:
        raise
    except httpx.HTTPError as error:
        raise OllamaPullError("ollama_pull_unreachable", str(error)) from error
