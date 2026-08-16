"""Provider Registry.

Design → docs/interfaces/provider.md "Provider Registry"

**Never silently degrade.** The worst outcome is "no sound, no reply" with an unknown
cause. Not-set-up (`ProviderNotConfigured`) and failed-to-start (`ProviderUnavailable`)
are **raised as distinct exceptions** (so callers can tailor guidance to the user).
"""

from __future__ import annotations

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
    """Selects one per kind. **UI for choosing the selection lands later in Phase 1 (settings UI).**"""

    __slots__ = ("_providers", "_selected")

    def __init__(self) -> None:
        self._providers: dict[ProviderKind, dict[str, Provider]] = {}
        self._selected: dict[ProviderKind, str] = {}

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

        `load()` is idempotent, so the caller doesn't need to track state.
        """
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
        for by_id in self._providers.values():
            for provider in by_id.values():
                if provider.is_loaded():
                    try:
                        await provider.unload()
                    except Exception:
                        log.exception("provider.unload_failed", provider=provider.id)
