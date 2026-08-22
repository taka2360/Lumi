"""Consent-gated Ollama model pull (ADR-037)."""

from __future__ import annotations

import httpx
import pytest

from lumi.setup.ollama import (
    QWEN_35_9B,
    OllamaModelArtifact,
    OllamaPullError,
    pull_ollama_model,
)


async def test_pull_uses_local_api_and_reports_progress() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=(
                b'{"status":"downloading","completed":3300000000,"total":6600000000}\n'
                b'{"status":"success"}\n'
            ),
        )

    progress: list[tuple[int, int]] = []

    async def report(completed: int, total: int) -> None:
        progress.append((completed, total))

    await pull_ollama_model(
        QWEN_35_9B,
        progress=report,
        transport=httpx.MockTransport(handler),
    )

    assert requests[0].url == "http://127.0.0.1:11434/api/pull"
    assert requests[0].method == "POST"
    assert b'"model":"qwen3.5:9b"' in requests[0].content
    assert progress == [(3_300_000_000, 6_600_000_000)]


async def test_pull_rejects_a_model_outside_the_displayed_catalog() -> None:
    arbitrary = OllamaModelArtifact("someone/unknown:latest", "Unknown", 1)

    with pytest.raises(OllamaPullError) as error:
        await pull_ollama_model(arbitrary)

    assert error.value.reason == "unknown_model"


async def test_pull_surfaces_ollamas_stream_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"error":"disk full"}\n')

    with pytest.raises(OllamaPullError) as error:
        await pull_ollama_model(QWEN_35_9B, transport=httpx.MockTransport(handler))

    assert error.value.reason == "ollama_pull_failed"
    assert "disk full" in error.value.detail
