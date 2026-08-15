"""Provider Registry。

設計 → docs/interfaces/provider.md「Provider Registry」

**黙って劣化しない。** 音声が出ない・返事が来ない、が原因不明になるのが最悪である。
未セットアップ（`ProviderNotConfigured`）と起動失敗（`ProviderUnavailable`）は
**別の例外として上げる**（呼び出し側がユーザーへの案内を変えられるように）。
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
    """kind ごとに1つ選択する。**選択の設定は Phase 1 後半（設定 UI）。**"""

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
        """選択されている Provider を返す。**未 load なら load する。**

        `load()` は冪等なので、呼び出し側が状態を気にしなくてよい。
        """
        provider = self.peek(kind)
        if not provider.is_loaded():
            await provider.load()
        return provider

    def peek(self, kind: ProviderKind) -> Provider:
        """**load せずに**取り出す。状態表示や `attribution()` に使う。"""
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
        """クレジット画面へ。**選択されているものだけ**を出す。

        使っていない Provider のクレジットを並べると、
        「何のクレジットなのか」が読み手に伝わらなくなる。
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
