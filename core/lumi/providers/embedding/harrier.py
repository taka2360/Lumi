"""Harrier-OSS-v1 270M on ONNX Runtime / CPU. **No torch, no transformers.**

Decision → ADR-041 / Interface → docs/interfaces/provider.md / Measured →
docs/measurements/phase2.md

## What the graph already does, and what it does not

Pooling (last non-padding token) and L2 normalization are **inside the exported graph**:
the output is `sentence_embedding`, shaped `[batch, 640]`, already unit length. There is
no pooling code here to get wrong.

What is not inside the graph is the instruction, and the padding side.

## Right padding only

Last-token pooling picks the last non-padding position using `attention_mask`. With the
padding on the left, the vector comes out different — **measured cosine 0.22 against the
same text unpadded** (2026-08-22). It does not raise; it is simply a different embedding
than the one stored for the same sentence. Every tensor here is built in one place for
that reason.

## Truncation keeps the last token

A sequence longer than the cap is cut and **the final id is forced back to EOS**, because
that is the token the pooling reads. Cutting without it would pool whatever word happened
to land at the boundary.

## Why the imports are deferred

`onnxruntime` costs visible startup time, and a session that never searches memory should
not pay it. The same reasoning as the STT Provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

from lumi import logging as lumi_logging
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderFailed,
    ProviderKind,
    ProviderNotConfigured,
    ResourceHint,
    UnloadPolicy,
)
from lumi.providers.embedding.base import Vector
from lumi.setup.install import is_model_installed
from lumi.setup.models import (
    EMBEDDING_MODEL_FILE,
    HARRIER_OSS_V1_270M,
    ModelArtifact,
    model_directory,
)

log = lumi_logging.get_logger(__name__)

#: Fixed by the model. **The `vec0` table is created with this** (ADR-041).
DIMENSION: Final = 640

#: The task description Lumi searches memory with. **Not a display string** — it is model
#: input, like the persona prompt, so it does not go through i18n.
#:
#: English, because the model card's own examples are, and the measured difference against
#: a Japanese wording was within the noise of a 10-item set (ADR-041).
MEMORY_TASK: Final = (
    "Given a message from the user, retrieve memories about the user that are relevant to it"
)

#: The wrapper the model was trained with. **Queries only.**
QUERY_TEMPLATE: Final = "Instruct: {task}\nQuery: {text}"

#: Where a sequence is cut. A memory is a sentence and an utterance is a turn; the model
#: accepts 32k, but **latency grows with length** (8 tokens 130 ms, 80 tokens 169 ms), and
#: nothing Lumi embeds is legitimately longer than this.
MAX_TOKENS: Final = 512

#: How many texts go into one `run`. **Batching is nearly free** — 8 short texts cost
#: 138 ms against 130 ms for one (2026-08-22), because the cost is per call, not per row.
#: Capped so that indexing a large memory set cannot build one enormous tensor.
BATCH_SIZE: Final = 16

#: ONNX Runtime threads. Left small: **what shares this CPU is capture, VAD and barge-in.**
INTRA_OP_THREADS: Final = 4


def as_query(text: str, *, task: str = MEMORY_TASK) -> str:
    """The query, wrapped in the instruction. **Pure.**"""
    return QUERY_TEMPLATE.format(task=task, text=text)


def to_tensors(encodings: Sequence[Sequence[int]], *, pad_id: int) -> tuple[np.ndarray, np.ndarray]:
    """Token ids as a right-padded batch, with its attention mask. **Pure.**

    **Right, never left** — see the module docstring. A left-padded batch produces valid,
    wrong vectors.
    """
    width = max(len(ids) for ids in encodings)
    tokens = np.full((len(encodings), width), pad_id, dtype=np.int64)
    mask = np.zeros((len(encodings), width), dtype=np.int64)
    for row, ids in enumerate(encodings):
        tokens[row, : len(ids)] = ids
        mask[row, : len(ids)] = 1
    return tokens, mask


def check_dimension(shape: Sequence[object], *, expected: int = DIMENSION) -> None:
    """Refuse a model whose output is not the width the vector table was built with.

    **Fail-closed, and here rather than at insert time.** `vec0` fixes the width at
    creation (ADR-041); a 768-wide vector reaching it fails as a blob-length error from
    inside SQLite, which names the storage layer for a problem that belongs to the model.

    A symbolic dimension (`"batch_size"`, `None`) is not a mismatch — it is the export
    saying it does not know, and nothing can be concluded from it.
    """
    if len(shape) != 2:
        return
    width = shape[1]
    if isinstance(width, int) and width != expected:
        raise ProviderFailed("embedding_dimension_mismatch", f"model={width} expected={expected}")


def truncate(ids: Sequence[int], *, limit: int, eos_id: int) -> list[int]:
    """Cut to `limit`, **keeping EOS last.** Pure.

    The pooling reads the final non-padding token. A plain slice would leave it pointing at
    whatever fell on the boundary, so the last position is put back.
    """
    if len(ids) <= limit:
        return list(ids)
    return [*ids[: limit - 1], eos_id]


def _required_token(tokenizer: Any, token: str) -> int:
    """The id of a token the padding and the pooling both depend on.

    **Fails closed.** Guessing 0 for `<pad>` would pad with whatever that id happens to
    mean, and guessing for `<eos>` would truncate onto the wrong token — in both cases the
    vectors come out valid, different, and unmatchable against the ones already stored.
    """
    found = tokenizer.token_to_id(token)
    if found is None:
        raise ProviderFailed("embedding_tokenizer_incomplete", f"missing {token}")
    return int(found)


class HarrierEmbeddingProvider:
    """Implementation of `EmbeddingProvider`. **CPU only; uses no VRAM.**"""

    kind = ProviderKind.EMBEDDING

    __slots__ = (
        "_artifact",
        "_eos_id",
        "_loading",
        "_model_dir",
        "_pad_id",
        "_session",
        "_tokenizer",
        "id",
    )

    def __init__(self, models_dir: Path, *, artifact: ModelArtifact = HARRIER_OSS_V1_270M) -> None:
        self.id = f"harrier:{artifact.name}"
        self._artifact = artifact
        self._model_dir = models_dir
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._pad_id = 0
        self._eos_id = 1
        #: **One session, even if two callers load at once.** The check before the `await`
        #: is not enough on its own: both would see `None` and both would build, and the
        #: loser's session — 200 MB of it — would sit allocated until the collector noticed.
        self._loading = asyncio.Lock()

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def load(self) -> None:
        """**Idempotent.** Raises `ProviderNotConfigured` when the model is not installed.

        **Never fetches.** Acquisition is Lumi's own setup path, with a pinned revision and
        a SHA-256 per file (ADR-023); a library quietly downloading 196 MiB would put the
        first external request before the user's consent.
        """
        # **Checked inside the lock, and only there.** Two callers that both look before
        # awaiting would both build; `load()` is called once at startup, so there is
        # nothing to buy by testing it outside first.
        async with self._loading:
            if self._session is not None:
                return
            session, tokenizer = await asyncio.to_thread(self._build)
            self._pad_id = _required_token(tokenizer, "<pad>")
            self._eos_id = _required_token(tokenizer, "<eos>")
            # **Assigned last.** `is_loaded()` and `_embed` read `_session`, so it becoming
            # non-`None` is what publishes the whole thing as ready.
            self._session, self._tokenizer = session, tokenizer
        log.info("embedding.loaded", provider=self.id, dimension=DIMENSION)

    async def unload(self) -> None:
        self._session = None
        self._tokenizer = None

    def is_loaded(self) -> bool:
        return self._session is not None

    def resource_hint(self) -> ResourceHint:
        return ResourceHint(
            device_pref=DevicePref.CPU_ONLY,
            # **Zero, and that is the point.** The GPU budget belongs to the LLM
            # (docs/DESIGN.md GPU strategy).
            vram_estimate_mb=0,
            load_time_estimate_ms=1500,
            unload_policy=UnloadPolicy.LRU,
        )

    def attribution(self) -> Attribution:
        """MIT imposes no credit obligation. **Returned anyway** — whether a credit is shown
        and whether the origin can be traced are different questions (docs/licensing.md §4.9).
        """
        return Attribution(
            display_name="Harrier-OSS-v1 270M",
            credit_text="microsoft/harrier-oss-v1-270m",
            license_name=self._artifact.license_name,
            license_url=self._artifact.license_url,
        )

    def dimension(self) -> int:
        return DIMENSION

    def model_id(self) -> str:
        """**Name and pinned revision.** Re-pinning invalidates the stored vectors, and this
        is what makes that detectable rather than a slow drift in search quality.
        """
        return f"{self._artifact.name}@{self._artifact.revision[:12]}"

    # ── embedding ──────────────────────────────────────────────────────────

    async def embed_query(self, text: str) -> Vector:
        """**The instruction is added here and nowhere else.**"""
        vectors = await self._embed([as_query(text)])
        return vectors[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """**No instruction.** What is stored has to be embedded the way it will be searched
        against, and this model was trained with the asymmetry.
        """
        if not texts:
            return []
        return await self._embed(list(texts))

    async def _embed(self, texts: list[str]) -> list[Vector]:
        if self._session is None:
            raise ProviderNotConfigured("embedding_not_loaded", self.id)
        vectors: list[Vector] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            vectors.extend(await asyncio.to_thread(self._run, batch))
        return vectors

    def _run(self, texts: list[str]) -> list[Vector]:
        assert self._session is not None and self._tokenizer is not None
        encodings = [
            truncate(self._tokenizer.encode(text).ids, limit=MAX_TOKENS, eos_id=self._eos_id)
            for text in texts
        ]
        tokens, mask = to_tensors(encodings, pad_id=self._pad_id)
        try:
            output = self._session.run(None, {"input_ids": tokens, "attention_mask": mask})[0]
        except Exception as error:
            raise ProviderFailed("embedding_failed", str(error)) from error
        return [np.asarray(row, dtype=np.float32) for row in output]

    # ── internals ──────────────────────────────────────────────────────────

    def _build(self) -> tuple[Any, Any]:
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:  # pragma: no cover - the dependency ships with Core
            raise ProviderNotConfigured("onnxruntime_missing", str(error)) from error

        directory = self._resolve()
        options = ort.SessionOptions()
        options.intra_op_num_threads = INTRA_OP_THREADS
        model = directory / EMBEDDING_MODEL_FILE
        try:
            session = ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])
            tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        except Exception as error:
            # Every pinned file was proved present just above, so this is an installation
            # that will not open — corrupt weights, an unsupported op, a truncated
            # external-data file. **Not "not set up yet"**, which asks something different
            # of the user (`providers/base.py`).
            raise ProviderFailed("embedding_model_unusable", str(error)) from error

        check_dimension(session.get_outputs()[0].shape)
        return session, tokenizer

    def _resolve(self) -> Path:
        if not is_model_installed(self._artifact, self._model_dir):
            raise ProviderNotConfigured(
                "embedding_model_missing", str(model_directory(self._artifact, self._model_dir))
            )
        return model_directory(self._artifact, self._model_dir)
