"""Job — foreground を取らない処理。

決定 → ADR-018 / docs/architecture/agent.md §5

Reflection・再埋め込み・DB メンテナンスは **Activity ではない。**
Activity にすると foreground を占有し、「Lumi は今なにをしているか」が
「記憶の整理」になってしまう。かといって同時実行を認めると Invariant 4 が意味を失う。

| | `Activity` | `Job` |
|---|---|---|
| foreground | 取る（1つだけ） | **取らない** |
| 発話 | する | **しない** |
| actor | 4種 | **`system` 固定 → L0 のみ** |
| barge-in | 対象になる | `uses_inference` なら**推論を明け渡す** |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from lumi.kernel.activity import Actor
from lumi.kernel.cancellation import Cancellation, CancelToken
from lumi.kernel.ids import JobId


class JobKind(StrEnum):
    #: Phase 2。セッション終了時 / 長いアイドル時に記憶を抽出する
    REFLECTION = "reflection"
    #: Phase 2。埋め込みモデルを変えたとき
    REEMBEDDING = "reembedding"
    MAINTENANCE = "maintenance"


@dataclass(slots=True)
class Job:
    """**`actor` は `system` 固定。** フィールドにしないのは、書き換えを型で塞ぐため。

    L1 以上のツールが必要なら、それは Job ではなく **Activity として propose すべき仕事**である
    （docs/architecture/agent.md §5 の規則4）。
    """

    id: JobId
    kind: JobKind
    cancellation: Cancellation
    #: True なら Arbiter から `inference_lease` を取る。foreground が推論を要求すると revoke される
    uses_inference: bool = False
    cancel_token: CancelToken = field(default_factory=CancelToken)

    @property
    def actor(self) -> Actor:
        return Actor.SYSTEM
