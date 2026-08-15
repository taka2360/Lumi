"""Policy の `decide()`。**docs/architecture/permission.md のテスト表 1〜4。**

`decide()` は純粋関数なので、**DB もイベントループも要らない。**
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from lumi.kernel.activity import Actor
from lumi.permission.grants import Grant, GrantId, GrantStore
from lumi.permission.policy import (
    Decision,
    Risk,
    decide,
    decide_with_rule,
    escalate_for_self_initiated,
)
from lumi.permission.scope import ScopeLane, SecurityScope
from lumi.provenance import TrustLevel

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
SCOPE = SecurityScope(lane=ScopeLane.FS, canonical="C:\\work\\a.txt")


def grant(scope: SecurityScope = SCOPE, *, uses: int | None = None) -> Grant:
    return Grant(
        id=GrantId("g1"),
        capability="fs.write",
        security_scope=scope,
        granted_at=NOW,
        remaining_uses=uses,
    )


# ── actor による昇格 ─────────────────────────────────────────


def test_self_initiated_l2_becomes_ask() -> None:
    assert decide(Risk.L2, Actor.SELF_INITIATED, TrustLevel.TRUSTED, None) is Decision.ASK


@pytest.mark.parametrize("risk", [Risk.L3, Risk.L4])
def test_self_initiated_l3_and_above_is_denied(risk: Risk) -> None:
    """**「厳しくなる」のではなく「できない」。**"""
    assert decide(risk, Actor.SELF_INITIATED, TrustLevel.TRUSTED, None) is Decision.DENY


def test_escalation_is_an_explicit_mapping() -> None:
    """「1段上がる」という説明は L3 で表と矛盾したため撤回した。"""
    assert escalate_for_self_initiated(Risk.L2) is Risk.L3
    assert escalate_for_self_initiated(Risk.L3) is Risk.DENIED
    assert escalate_for_self_initiated(Risk.L4) is Risk.DENIED


@pytest.mark.parametrize("risk", [Risk.L1, Risk.L2, Risk.L3, Risk.L4])
def test_system_actor_is_l0_only(risk: Risk) -> None:
    """**Job と idle は L0 のみ**（ADR-018）。"""
    assert decide(risk, Actor.SYSTEM, TrustLevel.TRUSTED, None) is Decision.DENY


def test_system_actor_may_do_l0() -> None:
    assert decide(Risk.L0, Actor.SYSTEM, TrustLevel.TRUSTED, None) is Decision.ALLOW


def test_scheduled_is_treated_like_user_initiated() -> None:
    assert decide(Risk.L1, Actor.SCHEDULED, TrustLevel.TRUSTED, None) is Decision.ALLOW


# ── provenance 昇格 ─────────────────────────────────────────


def test_tainted_context_escalates_l3_to_ask() -> None:
    """「Web ページを読んだあと、その内容に誘導されてファイルを消す」を防ぐ。"""
    assert decide(Risk.L3, Actor.USER_INITIATED, TrustLevel.TAINTED, None) is Decision.ASK


def test_provenance_rule_looks_at_effective_risk() -> None:
    """**`base_risk=L2` + `self_initiated` + `TAINTED` が `ask`。**

    `base_risk` を見ていたら L2 < L3 で不発火になる。
    **順序（actor 昇格が先）が守られていることの確認。**
    """
    decision, rule = decide_with_rule(Risk.L2, Actor.SELF_INITIATED, TrustLevel.TAINTED, None)
    assert decision is Decision.ASK
    assert rule == "provenance_escalation"


def test_tainted_does_not_escalate_below_l3() -> None:
    assert decide(Risk.L1, Actor.USER_INITIATED, TrustLevel.TAINTED, None) is Decision.ALLOW


# ── Grant ──────────────────────────────────────────────────


def test_l4_asks_even_with_a_grant() -> None:
    """**L4 は Grant があっても毎回聞く。**"""
    assert decide(Risk.L4, Actor.USER_INITIATED, TrustLevel.TRUSTED, grant()) is Decision.ASK


def test_l2_with_a_grant_is_allowed() -> None:
    assert decide(Risk.L2, Actor.USER_INITIATED, TrustLevel.TRUSTED, grant()) is Decision.ALLOW


def test_l2_without_a_grant_asks() -> None:
    assert decide(Risk.L2, Actor.USER_INITIATED, TrustLevel.TRUSTED, None) is Decision.ASK


def test_a_grant_does_not_apply_to_another_scope() -> None:
    """**「近い」では通さない**（fail-closed）。"""
    store = GrantStore()
    store.add(grant())
    other = SecurityScope(lane=ScopeLane.FS, canonical="C:\\work\\b.txt")
    assert store.find("fs.write", other, NOW) is None
    assert store.find("fs.write", SCOPE, NOW) is not None


def test_a_grant_expires() -> None:
    store = GrantStore()
    store.add(
        Grant(
            id=GrantId("g2"),
            capability="fs.write",
            security_scope=SCOPE,
            granted_at=NOW,
            expires_at=NOW,
        )
    )
    assert store.find("fs.write", SCOPE, NOW) is None


def test_remaining_uses_are_consumed() -> None:
    store = GrantStore()
    store.add(grant(uses=1))
    assert store.find("fs.write", SCOPE, NOW) is not None
    store.consume(GrantId("g1"))
    assert store.find("fs.write", SCOPE, NOW) is None


# ── 契約そのもの ────────────────────────────────────────────


def test_decide_takes_exactly_four_arguments() -> None:
    """**LLM の理由文・Tool の自己申告が届かないことを、シグネチャで保証する**（Invariant 1）。"""
    parameters = list(inspect.signature(decide).parameters)
    assert parameters == ["base_risk", "actor", "effective_trust", "grant"]


def test_decide_is_pure() -> None:
    """同じ引数なら常に同じ答え。`policy_version` の意味がここに懸かっている。"""
    args = (Risk.L2, Actor.SELF_INITIATED, TrustLevel.TAINTED, None)
    assert decide(*args) is decide(*args) is Decision.ASK
