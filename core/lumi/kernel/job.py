"""Job — processing that never takes foreground.

Decision → ADR-018 / docs/architecture/agent.md §5

Reflection, re-embedding, and DB maintenance are **not Activities.**
Making them Activities would occupy foreground, turning "what is Lumi doing right
now" into "tidying up memories." But allowing them to run concurrently would strip
Invariant 4 of its meaning.

| | `Activity` | `Job` |
|---|---|---|
| foreground | Takes it (only one) | **Never takes it** |
| Speaks | Yes | **No** |
| actor | 4 kinds | **Fixed to `system` → L0 only** |
| barge-in | Subject to it | **Yields inference** if `uses_inference` |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from lumi.kernel.activity import Actor
from lumi.kernel.cancellation import Cancellation, CancelToken
from lumi.kernel.ids import JobId


class JobKind(StrEnum):
    #: Phase 2. Extracts memories at session end / during a long idle period
    REFLECTION = "reflection"
    #: Phase 2. When the embedding model changes
    REEMBEDDING = "reembedding"
    MAINTENANCE = "maintenance"


@dataclass(slots=True)
class Job:
    """**`actor` is fixed to `system`.** It isn't a field so rewriting it is blocked at the type level.

    If L1-or-above tools are needed, that's not a Job — it's **work that should be
    proposed as an Activity instead** (docs/architecture/agent.md §5, rule 4).
    """

    id: JobId
    kind: JobKind
    cancellation: Cancellation
    #: If True, acquires an `inference_lease` from the Arbiter. Revoked if foreground requests inference
    uses_inference: bool = False
    cancel_token: CancelToken = field(default_factory=CancelToken)

    @property
    def actor(self) -> Actor:
        return Actor.SYSTEM
