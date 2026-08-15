"""Policy — **`decide()` がその唯一の定義である。**

唯一の定義場所 → docs/architecture/permission.md §2

> 表と散文はすべて `decide()` の**説明**であって、実装の根拠ではない。
> 表と `decide()` が食い違ったら `decide()` が正。

## 引数はこの4つだけ

`base_risk` / `actor` / `effective_trust` / `grant`。

**LLM の理由文・Tool の自己申告・Extension の reason は引数に含まれない**（Invariant 1, 3）。
「これは安全な操作です」という主張が判断に入る経路を、シグネチャの段階で塞ぐ。

## 規則の適用順序を関数の行順で固定する

「累積適用され、最も厳しいものが勝つ」だけでは、provenance 昇格が `base_risk` を見るのか
`effective_risk` を見るのかが決まらない。**`decide()` は effective_risk を見る**
（= actor 昇格を先に適用する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Final

from lumi.kernel.activity import Actor
from lumi.kernel.cancellation import Cancellation
from lumi.permission.grants import Grant
from lumi.provenance import TrustLevel

#: 監査ログに必ず入れる。**Policy は将来変わる。**
#: 「なぜこの操作を許可したのか」に答えるには、当時のルールが分からなければならない。
POLICY_VERSION: Final = "2026-08-16"


class Risk(IntEnum):
    """L の割り当ては Provisional。**L2/L3 の境界は使ってみないと分からない。**"""

    #: 読み取り・観測（screenshot, world 読み）
    L0 = 0
    #: ブラウザ閲覧・Web 検索
    L1 = 1
    #: ファイル読み取り・作業領域への書き込み
    L2 = 2
    #: アプリ起動・入力インジェクション・任意パス書き込み
    L3 = 3
    #: シェル実行・削除・外部送信・不可逆操作
    L4 = 4
    #: **実効リスクとしてのみ現れる。** Tool は宣言できない（登録時に弾く）
    DENIED = 5


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class SideEffect(StrEnum):
    NONE = "none"
    LOCAL = "local"
    EXTERNAL = "external"
    IRREVERSIBLE = "irreversible"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """Tool の静的な宣言。**Tool はこれを宣言するだけ。判断も正規化も検証もしない。**

    `lane` はここに持たせない。lane は「どの Canonicalizer / 検証器を使うか」という
    実行機構の選択であって、権限の宣言ではないため（重複して持つと齟齬の原因になる）。
    """

    capability: str
    risk: Risk
    reversible: bool
    side_effect: SideEffect
    cancellation: Cancellation


def escalate_for_self_initiated(base: Risk) -> Risk:
    """自律行動の実効リスク。**L3 以上は「昇格」ではなく拒否になる。**

    「1段上がる」という説明は L3 で表と矛盾したため撤回した
    （L3 self を1段上げると L4 の user 列 = ask になるが、表は deny）。
    明示的な写像として書くことで、この曖昧さを消す。
    """
    return {
        Risk.L0: Risk.L0,  # 読み取り・観測は自律でも許す
        Risk.L1: Risk.L1,  # ブラウザ閲覧は許す（AutonomyBudget が別途効く）
        Risk.L2: Risk.L3,  # 実効的に L3 = ask
        Risk.L3: Risk.DENIED,
        Risk.L4: Risk.DENIED,
        Risk.DENIED: Risk.DENIED,
    }[base]


def _evaluate(
    base_risk: Risk,
    actor: Actor,
    effective_trust: TrustLevel,
    grant: Grant | None,
) -> tuple[Decision, str]:
    """`decide()` の実装本体。**規則の識別子つき**（監査ログの `policy_rule_id`）。

    `decide()` と2重に規則を書かないために、判断はここ1箇所に閉じる。
    """
    # ── 1. 実効リスクを決める。actor による昇格はここで1回だけ起きる ──
    effective_risk = base_risk
    if actor is Actor.SELF_INITIATED:
        effective_risk = escalate_for_self_initiated(base_risk)

    # ── 2. 以降の規則はすべて effective_risk に対して適用する ──
    if actor is Actor.SYSTEM and base_risk > Risk.L0:
        return Decision.DENY, "system_actor_is_l0_only"

    if effective_risk is Risk.DENIED:
        return Decision.DENY, "self_initiated_denied"

    if effective_trust is TrustLevel.TAINTED and effective_risk >= Risk.L3:
        return Decision.ASK, "provenance_escalation"

    if effective_risk >= Risk.L4:
        return Decision.ASK, "l4_always_asks"

    if effective_risk >= Risk.L2 and grant is None:
        return Decision.ASK, "l2_without_grant"

    return Decision.ALLOW, "allow"


def decide(
    base_risk: Risk,
    actor: Actor,
    effective_trust: TrustLevel,
    grant: Grant | None,
) -> Decision:
    """**Policy の唯一の定義。純粋関数であること。**

    `Decision` を返す関数はこれ1つだけ（.claude/rules/00-invariants.md）。
    """
    return _evaluate(base_risk, actor, effective_trust, grant)[0]


def decide_with_rule(
    base_risk: Risk,
    actor: Actor,
    effective_trust: TrustLevel,
    grant: Grant | None,
) -> tuple[Decision, str]:
    """監査ログ用。**判断そのものは `decide()` と同じ実装**（`_evaluate`）を通る。"""
    return _evaluate(base_risk, actor, effective_trust, grant)
