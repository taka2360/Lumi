"""The embedding contract. **Asymmetric on purpose.**

Definition → docs/interfaces/provider.md / Decision → ADR-041

## Why there is no `embed(texts)`

The model Lumi uses wants an instruction on the query and nothing on the document:

```text
query:    "Instruct: {task}\nQuery: {text}"
document: "{text}"
```

A single symmetric method makes getting that wrong **completely silent**. Vectors still
come out, search still runs, results still look plausible — and the only symptom is that
Lumi seems a bit worse at remembering. Splitting the method is the cheapest way to turn a
quality regression nobody can see into a call nobody can write by accident.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

#: One embedding. **float32, L2-normalized**, so cosine similarity is a dot product.
Vector = np.ndarray


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors. **Never fetches its own model** (ADR-023)."""

    id: str

    async def embed_query(self, text: str) -> Vector:
        """Something the user said, as a search key. **The instruction is added here.**"""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """Things to be found later. **No instruction is added.**"""
        ...

    def dimension(self) -> int:
        """Fixed at the vector table's creation. **Changing it invalidates every row.**"""
        ...

    def model_id(self) -> str:
        """Stored per record as `embedding_model_id`.

        **Includes the pinned revision.** Vectors from two revisions of the same model are
        not comparable, and a bare model name would make that undetectable.
        """
        ...
