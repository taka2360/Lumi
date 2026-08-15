"""Provider の共通基底。

型の定義 → docs/interfaces/provider.md / 決定 → ADR-008

## `load` / `unload` / `resource_hint` を Phase 1 で入れる理由

Phase 1 に Vision は無いので `ModelResourceManager` は要らない（Phase 5）。
しかし**後から Provider にライフサイクルを追加すると全 Provider の書き換えになる。**
窓口だけ先に確保しておけば、Phase 5 は Manager を上に被せるだけで済む。

## `attribution()` を Phase 1 で入れる理由

**クレジット文字列を Core にハードコードすると、Provider を差し替えた瞬間に
クレジットが嘘になる。**「差し替え可能性がライセンスリスクの緩和策である」（ADR-008）
という主張は、差し替えたときに表示も追随して初めて成立する。

## 失敗の型を分ける

**「セットアップされていない」と「起動に失敗した」は違う**（docs/architecture/core.md §6）。
前者は「まだ導入されていない」、後者は「壊れている」であり、
**ユーザーに要求する行動が違う。** 1つの例外に潰すと、案内が嘘になる。
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
    #: 別プロセスで動く。Lumi の VRAM 予算の外にある
    EXTERNAL_PROCESS = "external_process"


class UnloadPolicy(StrEnum):
    PINNED = "pinned"
    LRU = "lru"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True, slots=True)
class ResourceHint:
    """Phase 5 の `ModelResourceManager` が読む。**Phase 1 では宣言するだけ。**"""

    device_pref: DevicePref
    #: 0 = GPU を使わない
    vram_estimate_mb: int
    load_time_estimate_ms: int
    unload_policy: UnloadPolicy


@dataclass(frozen=True, slots=True)
class Attribution:
    """クレジット表示に必要な情報。**Core はこれを解釈せず、そのまま Stage に渡す。**"""

    display_name: str
    #: 規約が要求する表記。例: "VOICEVOX:ずんだもん"
    credit_text: str
    license_name: str
    license_url: str | None = None
    homepage_url: str | None = None


class ProviderError(RuntimeError):
    """**理由を持つ。** 黙って劣化させないための土台。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class ProviderNotConfigured(ProviderError):
    """**まだ導入されていない。** ユーザーに求めるのは「セットアップ」。

    これを `ProviderUnavailable` と混ぜると、入っていないのに
    「起動してください」と案内することになる。
    """


class ProviderUnavailable(ProviderError):
    """**導入されているが使えない**（起動していない / バージョン不整合）。

    ユーザーに求めるのは「起動」または「修復」。
    """


class ProviderFailed(ProviderError):
    """推論中に失敗した。Activity は `failed` になり、Lumi は「うまくいかなかった」と言う。"""


class Provider(Protocol):
    id: str
    kind: ProviderKind

    async def load(self) -> None:
        """**冪等。** 2回呼んでも壊れない。"""
        ...

    async def unload(self) -> None: ...

    def resource_hint(self) -> ResourceHint: ...

    def is_loaded(self) -> bool: ...

    def attribution(self) -> Attribution: ...
