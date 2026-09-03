"""Sampling profiles → ADR-048 / docs/interfaces/provider.md.

**The thing worth testing is not the numbers, it is the omissions.** A field Lumi does not
send is a field `qwen3.5:9b`'s Modelfile decides (`temperature 1 / top_k 20 / top_p 0.95 /
presence_penalty 1.5`, verified 2026-09-02), so a profile that quietly stops stating one is
a profile that quietly hands that decision back — with nothing failing to say so.
"""

from __future__ import annotations

import json

import httpx
import pytest

from lumi.kernel.cancellation import CancelToken
from lumi.providers.llm.base import LLMOptions, Message
from lumi.providers.llm.ollama import OllamaProvider
from lumi.providers.llm.sampling import Purpose, options_for

#: Every field the conversation profile must pin down. **Leaving any of these out means
#: inheriting it from the model file**, which is the bug ADR-048 was written about
DECIDED = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repeat_penalty",
    "presence_penalty",
    "frequency_penalty",
)


@pytest.mark.parametrize("purpose", list(Purpose))
def test_a_known_family_leaves_nothing_to_the_model_file(purpose: Purpose) -> None:
    options = options_for("qwen3.5:9b", purpose)
    assert [f for f in DECIDED if getattr(options, f) is None] == []


def test_conversation_does_not_penalise_having_spoken_already() -> None:
    """★ `presence_penalty` is 0 **on purpose**, against Qwen's own card (which says 1.5).

    1.5 is aimed at endless repetition in long generations. A one-sentence spoken reply
    pays it on particles, the copula and the words the user just said instead — measured
    output in docs/measurements/phase2.md includes `clean な履歴` (language mixing, exactly
    the card's warning) and `休んでお休みして` (redundancy) at 1.5.
    """
    assert options_for("qwen3.5:9b", Purpose.CONVERSATION).presence_penalty == 0.0


def test_extraction_is_colder_than_conversation() -> None:
    """Extraction output is parsed, not heard. **`phase2.md` measured it at 0.2**, and the
    code had been running it at the conversation temperature ever since.
    """
    conversation = options_for("qwen3.5:9b", Purpose.CONVERSATION)
    extraction = options_for("qwen3.5:9b", Purpose.EXTRACTION)
    assert extraction.temperature < conversation.temperature
    # A JSON array repeats its own keys. Penalising that is penalising the format
    assert extraction.presence_penalty == 0.0


@pytest.mark.parametrize("model", ["qwen3:8b", "qwen3.5:9b", "qwen3.6:35b-a3b", "qwen3.8:27b"])
def test_the_whole_qwen3_family_takes_the_qwen_profile(model: str) -> None:
    assert options_for(model, Purpose.CONVERSATION).top_k == 20


@pytest.mark.parametrize("model", ["gemma3:12b", "llama3.1:8b", "qwen2.5:7b"])
def test_an_unmeasured_model_keeps_its_own_authors_values(model: str) -> None:
    """**Temperature only.** Qwen's numbers are a measurement of Qwen; applying them to a
    stranger is the same unfounded inheritance, chosen by Lumi instead of by Ollama.
    """
    options = options_for(model, Purpose.CONVERSATION)
    assert options.temperature == 0.7
    assert [f for f in DECIDED[1:] if getattr(options, f) is not None] == []


async def test_only_the_decided_fields_reach_ollama() -> None:
    """`None` must be **absent from the wire**, not sent as `null`.

    Ollama would parse a `null` back into its own zero value, so a profile that
    "sends nothing" by sending null is not leaving the model file's value in place —
    it is overwriting it with 0 while claiming not to.
    """
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.33.2"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})
        sent.append(json.loads(request.content))
        return httpx.Response(200, content='{"done": true}\n')

    provider = OllamaProvider("qwen3.5:9b", transport=httpx.MockTransport(handler))
    await provider.load()

    async for _ in provider.stream(
        [Message(role="user", content="やあ")],
        None,
        LLMOptions(model="qwen3.5:9b", temperature=0.7, top_p=0.8, seed=None),
        CancelToken(),
    ):
        pass

    options = [payload for payload in sent if payload["messages"]][-1]["options"]
    assert options == {"temperature": 0.7, "top_p": 0.8}


async def test_the_conversation_profile_is_sent_whole() -> None:
    """The profile is not advice. **Every value it decided has to be on the request.**"""
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.33.2"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})
        sent.append(json.loads(request.content))
        return httpx.Response(200, content='{"done": true}\n')

    provider = OllamaProvider("qwen3.5:9b", transport=httpx.MockTransport(handler))
    await provider.load()

    async for _ in provider.stream(
        [Message(role="user", content="やあ")],
        None,
        options_for("qwen3.5:9b", Purpose.CONVERSATION),
        CancelToken(),
    ):
        pass

    options = [payload for payload in sent if payload["messages"]][-1]["options"]
    assert options == {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "repeat_penalty": 1.0,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "num_predict": 512,
    }
