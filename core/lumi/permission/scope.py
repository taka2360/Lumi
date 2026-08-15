"""`SecurityScope` / `ScopeLane` / `Handle`。

型の定義 → docs/interfaces/tool.md / 契約 → docs/contracts/tool-execution.md

**Policy は `SecurityScope` に対してのみ適用される。生の引数には適用しない。**

```
Raw Input
   ↓  Canonicalizer[lane]   ← Kernel 所有
SecurityScope (immutable)
   ↓  Policy.decide
Decision
```

`lane` を `permission/` に置いているのは、**Canonicalizer と検証器の所有者が Kernel だから**
（docs/interfaces/tool.md「これらは `permission/` に置く。`tools/` ではない」）。
`tools/` は `permission/` を import してよいが、逆はしない。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Protocol


class ScopeLane(StrEnum):
    """どの Canonicalizer / 検証器を使うか。**Class の別も lane から決まる。**"""

    # ── Class A: in-core のみ。Handle 契約 ──────
    FS = "fs"
    PROCESS = "process"
    INPUT = "input"
    #: screenshot 等の読み取り
    DESKTOP = "desktop"
    SYSTEM = "system"
    #: Lumi の記憶操作
    MEMORY = "memory"
    #: 表情・モーション
    CHARACTER = "character"

    # ── Class B: out-of-process。事後検証契約 ───
    BROWSER = "browser"
    GAME = "game"
    WIDGET = "widget"


class ToolClass(StrEnum):
    """`BindVerifier` が成立するのは Handle が Core のプロセス内にある場合だけ（ADR-017）。"""

    #: canonicalize → decide → **bind → verify** → execute
    A = "A"
    #: canonicalize → decide → invoke → **verify_result**（TOCTOU は防止できない。検出のみ）
    B = "B"


#: **Kernel が所有する。Tool は自分の Class を宣言しない**（自己申告させない）。
LANE_CLASS: Final[dict[ScopeLane, ToolClass]] = {
    ScopeLane.FS: ToolClass.A,
    ScopeLane.PROCESS: ToolClass.A,
    ScopeLane.INPUT: ToolClass.A,
    ScopeLane.DESKTOP: ToolClass.A,
    ScopeLane.SYSTEM: ToolClass.A,
    ScopeLane.MEMORY: ToolClass.A,
    ScopeLane.CHARACTER: ToolClass.A,
    ScopeLane.BROWSER: ToolClass.B,
    ScopeLane.GAME: ToolClass.B,
    ScopeLane.WIDGET: ToolClass.B,
}


#: **lane ごとに「結果が Lumi の外から来た生データか」を Kernel が決める。**
#: Tool に自己申告させない（`ToolResult` の provenance は Core が付与する）。
#:
#: `character` を False にしているのは、表情の変更結果が外界の観測ではないため。
#: True にすると**表情を変えるたびにセッションが tainted になり**、
#: provenance 昇格（実効 L3 以上を ask にする規則）が判別力を失う。
LANE_RESULT_IS_EXTERNAL: Final[dict[ScopeLane, bool]] = {
    ScopeLane.FS: True,
    ScopeLane.PROCESS: True,
    #: 注入の成否を返すだけで、外界の内容を持ち帰らない
    ScopeLane.INPUT: False,
    #: screenshot は外界そのもの
    ScopeLane.DESKTOP: True,
    ScopeLane.SYSTEM: True,
    #: Phase 2。`user_confirmed` な記憶の扱いは MemoryStore 側で決める
    ScopeLane.MEMORY: False,
    ScopeLane.CHARACTER: False,
    ScopeLane.BROWSER: True,
    ScopeLane.GAME: True,
    ScopeLane.WIDGET: True,
}


@dataclass(frozen=True, slots=True)
class SecurityScope:
    """**不変。** Policy が検査した後に書き換わりうると、TOCTOU が成立する。

    生成できるのは `Canonicalizer`（Kernel 所有）だけ。Tool も LLM も作れない。
    """

    lane: ScopeLane
    #: 正規化済みの対象（絶対パス / 正規化 URL / 実行ファイル実体パス）
    canonical: str
    #: lane 固有（HWND, PID など）。**監査に出るので、機密を入れない**
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Handle(Protocol):
    """`SecurityScope` が指す対象への安定した参照。

    **`BindVerifier.verify()` を通るまで、Handle は有効ではない。**
    `Tool.bind` が返した直後の Handle を `execute` に渡してはならない（Kernel が間に入る）。
    """

    @property
    def scope(self) -> SecurityScope: ...

    def close(self) -> None: ...
