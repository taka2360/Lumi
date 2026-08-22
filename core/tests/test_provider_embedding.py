"""The embedding Provider. **docs/interfaces/provider.md / ADR-041.**

Two of these are the whole reason the Provider is shaped the way it is:

* **The instruction goes on the query and not on the document.** Getting it backwards is
  silent — vectors still come out, search still runs, results still look plausible.
* **Padding goes on the right.** Left-padding produces a valid, different vector for the
  same sentence (measured cosine 0.22), and nothing raises.

Neither can be caught by a test that only checks "an embedding came back", so they are
checked on the tensors themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from lumi.providers.base import ProviderFailed, ProviderNotConfigured
from lumi.providers.embedding import harrier
from lumi.providers.embedding.harrier import (
    DIMENSION,
    MAX_TOKENS,
    HarrierEmbeddingProvider,
    as_query,
    to_tensors,
    truncate,
)
from lumi.setup.models import EMBEDDING_MODEL_FILE, HARRIER_OSS_V1_270M, model_directory

PAD = 0
EOS = 1


class FakeTokenizer:
    """One token per character, wrapped in BOS/EOS the way the real one does."""

    def encode(self, text: str) -> Any:
        ids = [2, *[ord(character) for character in text], EOS]
        return type("Encoding", (), {"ids": ids})()

    def token_to_id(self, token: str) -> int | None:
        return {"<pad>": PAD, "<eos>": EOS}.get(token)


class FakeSession:
    """Records what it was asked to run, and answers with unit vectors."""

    def __init__(self, dimension: int = DIMENSION) -> None:
        self.calls: list[dict[str, np.ndarray]] = []
        self._dimension = dimension

    def get_outputs(self) -> list[Any]:
        return [type("Output", (), {"shape": ["batch_size", self._dimension]})()]

    def run(self, _outputs: None, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.calls.append(feed)
        rows = feed["input_ids"].shape[0]
        vectors = np.zeros((rows, self._dimension), dtype=np.float32)
        vectors[:, 0] = 1.0
        return [vectors]


def provider(session: FakeSession, tmp_path: Path) -> HarrierEmbeddingProvider:
    instance = HarrierEmbeddingProvider(tmp_path)
    instance._session = session
    instance._tokenizer = FakeTokenizer()
    instance._pad_id = PAD
    instance._eos_id = EOS
    return instance


def texts_of(feed: dict[str, np.ndarray]) -> list[str]:
    """The batch, back as strings. The fake tokenizer is code points, so this inverts it."""
    return [
        "".join(
            chr(token)
            for token, keep in zip(row, mask, strict=True)
            if keep and token not in (2, EOS)
        )
        for row, mask in zip(feed["input_ids"], feed["attention_mask"], strict=True)
    ]


# ── The asymmetry ────────────────────────────────────────────


async def test_a_query_carries_the_instruction(tmp_path: Path) -> None:
    """★ **Queries only.** The model was trained with it; without it, retrieval quality
    drops and nothing says so.
    """
    session = FakeSession()

    await provider(session, tmp_path).embed_query("猫は元気?")

    sent = texts_of(session.calls[0])[0]
    assert sent.startswith("Instruct: ")
    assert "\nQuery: 猫は元気?" in sent


async def test_a_document_carries_no_instruction(tmp_path: Path) -> None:
    """★ The other half. A document embedded through the query path is a **different
    vector for the same sentence**, and every search against it is quietly worse.
    """
    session = FakeSession()

    await provider(session, tmp_path).embed_documents(["ユーザーは猫を飼っている"])

    assert texts_of(session.calls[0]) == ["ユーザーは猫を飼っている"]


def test_the_instruction_is_a_pure_function() -> None:
    assert as_query("猫", task="find things") == "Instruct: find things\nQuery: 猫"


async def test_embedding_nothing_calls_nothing(tmp_path: Path) -> None:
    """An empty index pass should not build a zero-row tensor and hand it to ONNX."""
    session = FakeSession()

    assert await provider(session, tmp_path).embed_documents([]) == []
    assert session.calls == []


# ── Tensors ──────────────────────────────────────────────────


def test_padding_goes_on_the_right() -> None:
    """★ **Last-token pooling reads the last non-padding position.** With the padding on
    the left the model pools a different token, returns a perfectly valid vector, and the
    stored embedding for that sentence no longer matches the one search computes.
    """
    tokens, mask = to_tensors([[2, 9, EOS], [2, 9, 9, 9, EOS]], pad_id=PAD)

    assert tokens[0].tolist() == [2, 9, EOS, PAD, PAD]
    assert mask[0].tolist() == [1, 1, 1, 0, 0]
    assert mask[1].tolist() == [1, 1, 1, 1, 1]


def test_a_batch_of_one_is_not_padded() -> None:
    tokens, mask = to_tensors([[2, 9, EOS]], pad_id=PAD)

    assert tokens.shape == (1, 3)
    assert mask.sum() == 3


def test_truncation_keeps_the_last_token_last() -> None:
    """★ A plain slice would leave the pooled position on whatever fell at the boundary."""
    long = [2, *range(10, 40), EOS]

    cut = truncate(long, limit=8, eos_id=EOS)

    assert len(cut) == 8
    assert cut[-1] == EOS
    assert cut[:7] == long[:7]


def test_short_input_is_left_alone() -> None:
    assert truncate([2, 9, EOS], limit=8, eos_id=EOS) == [2, 9, EOS]


async def test_a_long_text_is_cut_before_it_reaches_the_model(tmp_path: Path) -> None:
    session = FakeSession()

    await provider(session, tmp_path).embed_documents(["あ" * (MAX_TOKENS * 2)])

    assert session.calls[0]["input_ids"].shape[1] == MAX_TOKENS


# ── Batching ─────────────────────────────────────────────────


async def test_documents_are_batched(tmp_path: Path) -> None:
    """Batching is nearly free (8 texts cost what 1 costs), so indexing does not pay per
    row — but the batch is capped so a large memory set cannot build one huge tensor.
    """
    session = FakeSession()

    vectors = await provider(session, tmp_path).embed_documents(
        [f"memory {index}" for index in range(harrier.BATCH_SIZE + 3)]
    )

    assert len(vectors) == harrier.BATCH_SIZE + 3
    assert [call["input_ids"].shape[0] for call in session.calls] == [harrier.BATCH_SIZE, 3]


async def test_every_vector_is_float32(tmp_path: Path) -> None:
    """What sqlite-vec stores. A float64 row would be silently wrong-sized on insert."""
    session = FakeSession()

    vectors = await provider(session, tmp_path).embed_documents(["猫"])

    assert vectors[0].dtype == np.float32
    assert vectors[0].shape == (DIMENSION,)


# ── Failing loudly ───────────────────────────────────────────


async def test_embedding_before_loading_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProviderNotConfigured):
        await HarrierEmbeddingProvider(tmp_path).embed_query("猫")


async def test_a_missing_model_is_not_configured_rather_than_broken(tmp_path: Path) -> None:
    """★ "Not set up" and "broken" ask different things of the user
    (`lumi/providers/base.py`). **Nothing is ever fetched here** (ADR-023).
    """
    with pytest.raises(ProviderNotConfigured) as raised:
        await HarrierEmbeddingProvider(tmp_path).load()

    assert raised.value.reason == "embedding_model_missing"
    assert not model_directory(HARRIER_OSS_V1_270M, tmp_path).exists()


async def test_a_failing_session_is_a_provider_failure(tmp_path: Path) -> None:
    class Broken(FakeSession):
        def run(self, _outputs: None, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
            raise RuntimeError("op not implemented")

    with pytest.raises(ProviderFailed):
        await provider(Broken(), tmp_path).embed_query("猫")


def test_the_model_id_carries_the_pinned_revision(tmp_path: Path) -> None:
    """★ Vectors from two revisions are not comparable. **A bare name would make a
    re-pin look like a gradual loss of search quality** instead of a detectable mismatch.
    """
    identifier = HarrierEmbeddingProvider(tmp_path).model_id()

    assert identifier.startswith("harrier-oss-v1-270m@")
    assert HARRIER_OSS_V1_270M.revision.startswith(identifier.split("@")[1])


def test_the_provider_asks_for_no_vram(tmp_path: Path) -> None:
    """**The GPU budget belongs to the LLM** (docs/DESIGN.md GPU strategy)."""
    assert HarrierEmbeddingProvider(tmp_path).resource_hint().vram_estimate_mb == 0


def test_the_pinned_files_are_what_the_provider_opens() -> None:
    """The graph is a stub; **its weights are the `.onnx_data` beside it**, resolved by
    name. Pinning one without the other installs something that cannot open.
    """
    names = {file.name for file in HARRIER_OSS_V1_270M.files}

    assert EMBEDDING_MODEL_FILE in names
    assert f"{EMBEDDING_MODEL_FILE}_data" in names
    assert "tokenizer.json" in names


def test_the_dimension_matches_what_the_vector_table_will_be_created_with() -> None:
    """`vec0` fixes the width at creation (ADR-041 / docs/architecture/memory.md §2)."""
    assert DIMENSION == 640
