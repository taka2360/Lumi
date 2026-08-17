"""Provider Registry.

Design → docs/interfaces/provider.md "Provider Registry"

**Never silently degrade.** The worst outcome is "no sound, no reply" with an unknown
cause. Not-set-up (`ProviderNotConfigured`) and failed-to-start (`ProviderUnavailable`)
are **raised as distinct exceptions** (so callers can tailor guidance to the user).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from lumi import logging as lumi_logging
from lumi.providers.base import Attribution, Provider, ProviderKind, ProviderNotConfigured

log = lumi_logging.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    id: str
    kind: ProviderKind
    loaded: bool


class ProviderRegistry:
    """Selects one per kind. **UI for choosing lands later in Phase 1 (settings UI).**"""

    __slots__ = ("_loading", "_providers", "_selected")

    def __init__(self) -> None:
        self._providers: dict[ProviderKind, dict[str, Provider]] = {}
        self._selected: dict[ProviderKind, str] = {}
        #: One in-flight `load()` per kind. **Created lazily** so a Registry can be built
        #: outside a running loop
        self._loading: dict[ProviderKind, asyncio.Lock] = {}

    def register(self, provider: Provider, *, select: bool = True) -> None:
        by_id = self._providers.setdefault(provider.kind, {})
        by_id[provider.id] = provider
        if select or provider.kind not in self._selected:
            self._selected[provider.kind] = provider.id

    def select(self, kind: ProviderKind, provider_id: str) -> None:
        if provider_id not in self._providers.get(kind, {}):
            raise ProviderNotConfigured(f"{kind}:{provider_id} は登録されていない")
        self._selected[kind] = provider_id

    async def get(self, kind: ProviderKind) -> Provider:
        """Returns the selected Provider. **Loads it if not yet loaded.**

        **Idempotent is not the same as concurrency-safe.** A `load()` that starts an
        external engine takes ~14 seconds, and every turn arriving in that window sees
        `is_loaded() == False`. Without serializing, four utterances in a row started
        **four AivisSpeech processes, each holding 1 GB of VRAM** (observed 2026-08-17) —
        precisely the VRAM the LLM is supposed to get (DESIGN.md §7).

        Serialized per kind rather than globally: waiting for the TTS engine must not
        also delay the STT model.
        """
        provider = self.peek(kind)
        if provider.is_loaded():
            return provider
        async with self._loading.setdefault(kind, asyncio.Lock()):
            # **Re-checked**: whoever held the lock has very likely just loaded it.
            # Re-`peek` too, since `select()` may have moved while we waited
            provider = self.peek(kind)
            if not provider.is_loaded():
                await provider.load()
            return provider

    def peek(self, kind: ProviderKind) -> Provider:
        """Retrieves it **without loading.** Used for status display and `attribution()`."""
        provider_id = self._selected.get(kind)
        if provider_id is None:
            raise ProviderNotConfigured(f"{kind} の Provider が登録されていない")
        return self._providers[kind][provider_id]

    def has(self, kind: ProviderKind) -> bool:
        return kind in self._selected

    def available(self, kind: ProviderKind) -> list[ProviderInfo]:
        return [
            ProviderInfo(id=provider.id, kind=kind, loaded=provider.is_loaded())
            for provider in self._providers.get(kind, {}).values()
        ]

    def attributions(self) -> list[Attribution]:
        """For the credits screen. Emits **only the ones currently selected.**

        Listing credits for unused Providers would leave the reader unable to tell
        what each credit is actually for.
        """
        return [self.peek(kind).attribution() for kind in sorted(self._selected)]

    async def unload_all(self) -> None:
        """**Unloads everything, loaded or not.**

        `is_loaded()` means "usable," not "owns nothing." A Provider that owns an
        external process can be holding one it started while `load()` was interrupted
        partway (cancelled, or the engine never answered). Skipping those **leaves the
        process behind on exit**, which is exactly what docs/architecture/core.md §6
        forbids. `unload()` is idempotent, so calling it unconditionally is safe.
        """
        for by_id in self._providers.values():
            for provider in by_id.values():
                try:
                    await provider.unload()
                except Exception:
                    log.exception("provider.unload_failed", provider=provider.id)
