"""Common base for Providers.

Type definitions → docs/interfaces/provider.md / Decision → ADR-008

## Why `load` / `unload` / `resource_hint` are added in Phase 1

Phase 1 has no Vision, so `ModelResourceManager` isn't needed yet (Phase 5).
But **adding a lifecycle to Provider later would mean rewriting every Provider.**
Reserving the interface now means Phase 5 can just layer a Manager on top.

## Why `attribution()` is added in Phase 1

**Hardcoding the credit string into Core makes the credit false the instant the
Provider is swapped out.** The claim that "swappability mitigates license risk"
(ADR-008) only holds if the displayed credit follows along when the swap happens.

## Separate failure types

**"Not set up" and "failed to start" are different things** (docs/architecture/core.md §6).
The former is "not yet installed," the latter is "broken," and
**what the user needs to do about them differs.** Collapsing them into one exception
makes the guidance shown to the user wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ProviderKind(StrEnum):
    LLM = "llm"
    STT = "stt"
    TTS = "tts"
    EMBEDDING = "embedding"
    VISION = "vision"


class DevicePref(StrEnum):
    GPU_REQUIRED = "gpu_required"
    GPU_PREFERRED = "gpu_preferred"
    CPU_ONLY = "cpu_only"
    #: Runs in a separate process. Outside Lumi's VRAM budget
    EXTERNAL_PROCESS = "external_process"


class UnloadPolicy(StrEnum):
    PINNED = "pinned"
    LRU = "lru"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True, slots=True)
class ResourceHint:
    """Read by Phase 5's `ModelResourceManager`. **In Phase 1, only declared.**"""

    device_pref: DevicePref
    #: 0 = doesn't use GPU
    vram_estimate_mb: int
    load_time_estimate_ms: int
    unload_policy: UnloadPolicy


@dataclass(frozen=True, slots=True)
class Attribution:
    """Information needed for the credit display. **Core doesn't interpret this — it's passed straight to the Stage.**"""

    display_name: str
    #: The exact wording required by the license. e.g. "VOICEVOX:Zundamon"
    credit_text: str
    license_name: str
    license_url: str | None = None
    homepage_url: str | None = None


class ProviderError(RuntimeError):
    """**Carries a reason.** The foundation for never silently degrading."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class ProviderNotConfigured(ProviderError):
    """**Not yet installed.** What's asked of the user is "set it up."

    Mixing this with `ProviderUnavailable` would tell the user to "please start it"
    when it isn't even installed.
    """


class ProviderUnavailable(ProviderError):
    """**Installed but unusable** (not running / version mismatch).

    What's asked of the user is "start it" or "repair it."
    """


class ProviderFailed(ProviderError):
    """Failed during inference. The Activity becomes `failed`, and Lumi says "that didn't work."""


class Provider(Protocol):
    id: str
    kind: ProviderKind

    async def load(self) -> None:
        """**Idempotent.** Calling it twice doesn't break anything."""
        ...

    async def unload(self) -> None: ...

    def resource_hint(self) -> ResourceHint: ...

    def is_loaded(self) -> bool: ...

    def attribution(self) -> Attribution: ...
