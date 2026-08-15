"""信頼の追跡と伝播 — [Invariant 3](../../docs/contracts/invariants.md) / 7 の型による実装。

規則の唯一の定義場所 → docs/contracts/provenance.md

## なぜトップレベルに居るのか

`Signal` が `trust_level` を持つ（docs/contracts/event-model.md）ので、
`kernel/` はこの型に依存する。`memory/` の下に置くと **kernel → memory の逆依存**が生まれ、
「kernel は他に依存しない」が破れる。

trust の型は kernel・permission・tools・agent・memory の**すべてが使う横断的な制約**であり、
どれか1つの下に置くと「そのモジュールの持ち物」に見えてしまう。
→ docs/architecture/core.md §4

## 実装上の絶対条件

**`TrustLevel.TRUSTED` を代入してよいのは2箇所だけ。**

1. ユーザーの直接入力を受け取るハンドラ（音声入力・テキスト入力・UI 操作）
2. 記憶 UI のユーザー確認ハンドラ（Phase 2）

**自動昇格の実装を作らない。** ここにある `join` / `taint` / `propagate` は
**汚染を伝播させるための関数であって、昇格の経路ではない**（入力が全て trusted のときだけ
trusted を返す）。これは grep とテストで検証する。
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Protocol


class ProvenanceClass(StrEnum):
    """ラベル。**監査とユーザーへの説明のため**であって、Policy を緩めるためではない。"""

    #: ユーザーの直接入力 / Lumi 内部状態 / システム設定 / user_confirmed な記憶
    TRUSTED = "trusted"
    #: 外部由来の生データ。Web 本文・ファイル・Vision 結果・ゲーム画面・Extension 出力
    UNTRUSTED = "untrusted"
    #: untrusted を入力に含む処理の出力。要約・抽出された記憶・推論結果
    DERIVED = "derived"


class TrustLevel(StrEnum):
    """Policy 判断用。join-semilattice（`trusted ⊑ tainted`）。"""

    TRUSTED = "trusted"
    TAINTED = "tainted"


class Provenanced(Protocol):
    """provenance を持つレコード。**粒度は文字単位ではなくレコード単位。**"""

    @property
    def provenance_class(self) -> ProvenanceClass: ...

    @property
    def trust_level(self) -> TrustLevel: ...


def taint(cls: ProvenanceClass) -> TrustLevel:
    """ラベルを Policy 判断用の2値に落とす。

    **`DERIVED` も `TAINTED` になる。** これが Invariant 7 の核心であり、
    「悪意あるページの要約は生ページより安全そう」という直感を否定している。
    攻撃者は要約を生き延びるペイロードを作れるし、格下げを許せばロンダリング経路ができる。
    """
    return TrustLevel.TRUSTED if cls is ProvenanceClass.TRUSTED else TrustLevel.TAINTED


def join(a: TrustLevel, b: TrustLevel) -> TrustLevel:
    """束の join。**片方でも汚染されていれば汚染。**"""
    if a is TrustLevel.TAINTED or b is TrustLevel.TAINTED:
        return TrustLevel.TAINTED
    return TrustLevel.TRUSTED


def join_all(levels: Iterable[TrustLevel]) -> TrustLevel:
    """空なら `TRUSTED`。**入力が無いことは汚染されていないことを意味する**（単位元）。"""
    result = TrustLevel.TRUSTED
    for level in levels:
        result = join(result, level)
    return result


def propagate(inputs: Iterable[Provenanced], *, is_raw_external: bool) -> ProvenanceClass:
    """処理の出力に付ける `ProvenanceClass` を決める。**迷ったら汚染側に倒す。**

    `is_raw_external=True` は「**Lumi の外の世界から取ってきた生データ**」を意味する。
    LLM の出力にこれを立ててはならない — LLM 出力は Lumi が組み立てたプロンプトの関数であって、
    外界の観測ではない。立てると、雑談ターンまで汚染されて provenance 昇格が判別力を失う。
    """
    if is_raw_external:
        return ProvenanceClass.UNTRUSTED
    if all(i.provenance_class is ProvenanceClass.TRUSTED for i in inputs):
        return ProvenanceClass.TRUSTED
    return ProvenanceClass.DERIVED


def propagate_trust(inputs: Iterable[Provenanced]) -> TrustLevel:
    """処理の出力の `TrustLevel`。入力の join。"""
    return join_all(i.trust_level for i in inputs)
