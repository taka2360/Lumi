"""Kernel 実行契約と、登録時の fail-closed。

**docs/contracts/tool-execution.md テスト 1 / 3〜8。**
**docs/architecture/permission.md テスト 5 / 13。**

> **契約: Policy が検査した対象と、execute が操作した対象は同一でなければならない。**
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from lumi.character import Emotion, ExpressionIntent
from lumi.kernel.activity import Actor
from lumi.kernel.cancellation import Cancellation, CancelToken
from lumi.kernel.event import EventBus
from lumi.kernel.hooks import Continue, HookName, HookRegistry, Veto
from lumi.kernel.ids import new_activity_id, new_correlation_id
from lumi.permission.grants import GrantStore
from lumi.permission.kernel import PermissionKernel
from lumi.permission.policy import PermissionSpec, Risk, SideEffect
from lumi.permission.scope import ScopeLane, SecurityScope
from lumi.permission.verifiers import (
    CHARACTER_SELF,
    CharacterBindVerifier,
    CharacterCanonicalizer,
)
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.audit import SqliteAuditLog
from lumi.storage.events import SqliteEventStore
from lumi.storage.sqlite import Database
from lumi.tools.base import ToolContext, ToolKind, ToolOutcome
from lumi.tools.builtin.character import CharacterHandle, SetExpressionTool
from lumi.tools.registry import ToolRegistrationError, ToolRegistry


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database.open(":memory:")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def hooks() -> HookRegistry:
    return HookRegistry()


@pytest.fixture
def registry(database: Database, hooks: HookRegistry) -> ToolRegistry:
    return ToolRegistry(
        PermissionKernel(GrantStore(), SqliteAuditLog(database)),
        EventBus(SqliteEventStore(database)),
        hooks,
        canonicalizers={ScopeLane.CHARACTER: CharacterCanonicalizer()},
        bind_verifiers={ScopeLane.CHARACTER: CharacterBindVerifier()},
    )


def context(
    actor: Actor = Actor.USER_INITIATED, trust: TrustLevel = TrustLevel.TRUSTED
) -> ToolContext:
    return ToolContext(
        cancel_token=CancelToken(),
        actor=actor,
        activity_id=new_activity_id(),
        correlation_id=new_correlation_id(),
        input_trust_level=trust,
    )


class Recorder:
    """Stage への送信の代わり。**Tool は WS を知らない。**"""

    def __init__(self) -> None:
        self.sent: list[ExpressionIntent] = []

    async def __call__(self, intent: ExpressionIntent) -> None:
        self.sent.append(intent)


# ── 登録時の fail-closed ─────────────────────────────────────


def test_registering_without_a_canonicalizer_fails(database: Database) -> None:
    """**lane に対応する Canonicalizer が無い Tool は登録できない。**"""
    empty = ToolRegistry(
        PermissionKernel(GrantStore(), SqliteAuditLog(database)),
        EventBus(SqliteEventStore(database)),
        HookRegistry(),
    )
    with pytest.raises(ToolRegistrationError):
        empty.register(SetExpressionTool(Recorder()))


def test_registering_without_a_bind_verifier_fails(database: Database) -> None:
    partial = ToolRegistry(
        PermissionKernel(GrantStore(), SqliteAuditLog(database)),
        EventBus(SqliteEventStore(database)),
        HookRegistry(),
        canonicalizers={ScopeLane.CHARACTER: CharacterCanonicalizer()},
    )
    with pytest.raises(ToolRegistrationError):
        partial.register(SetExpressionTool(Recorder()))


def test_non_cancellable_with_side_effects_needs_l3(registry: ToolRegistry) -> None:
    """**キャンセルできない副作用を、ユーザー確認なしに起こさせない。**"""

    class Risky(SetExpressionTool):
        permission = PermissionSpec(
            capability="character.expression",
            risk=Risk.L2,
            reversible=False,
            side_effect=SideEffect.LOCAL,
            cancellation=Cancellation.NON_CANCELLABLE,
        )

    with pytest.raises(ToolRegistrationError):
        registry.register(Risky(Recorder()))


def test_in_core_tool_cannot_claim_a_class_b_lane(registry: ToolRegistry) -> None:
    """**Handle 契約が成立するのは in-core だけ**（ADR-017）。"""

    class Pretender(SetExpressionTool):
        lane = ScopeLane.BROWSER

    with pytest.raises(ToolRegistrationError):
        registry.register(Pretender(Recorder()))


def test_denied_cannot_be_declared_as_a_risk(registry: ToolRegistry) -> None:
    """`DENIED` は**実効リスクとしてのみ現れる値**であって、宣言できるものではない。"""

    class Impossible(SetExpressionTool):
        permission = PermissionSpec(
            capability="x",
            risk=Risk.DENIED,
            reversible=True,
            side_effect=SideEffect.NONE,
            cancellation=Cancellation.HARD,
        )

    with pytest.raises(ToolRegistrationError):
        registry.register(Impossible(Recorder()))


def test_duplicate_names_are_rejected(registry: ToolRegistry) -> None:
    registry.register(SetExpressionTool(Recorder()))
    with pytest.raises(ToolRegistrationError):
        registry.register(SetExpressionTool(Recorder()))


# ── Kernel 実行契約 ─────────────────────────────────────────


async def test_a_l0_tool_runs_through_the_full_path(registry: ToolRegistry) -> None:
    sender = Recorder()
    registry.register(SetExpressionTool(sender))

    result = await registry.invoke(
        "character.set_expression", context(), {"emotion": "happy", "intensity": 0.5}
    )

    assert result.ok
    assert sender.sent == [ExpressionIntent(emotion=Emotion.HAPPY, intensity=0.5)]


async def test_canonicalization_failure_is_denied(registry: ToolRegistry) -> None:
    """**「よく分からないので通す」経路を作らない**（fail-closed）。"""
    registry.register(SetExpressionTool(Recorder()))
    result = await registry.invoke(
        "character.set_expression", context(), {"target": "someone_else", "emotion": "happy"}
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "canonicalization_failed"


async def test_bind_verifier_detects_a_lying_tool(registry: ToolRegistry) -> None:
    """**これが契約の要**（tool-execution.md テスト1）。

    `Tool.bind` は Tool が実装する。**scope とは違う対象の Handle を返したら、
    `execute` はその対象を操作してしまう。** `BindVerifier` が Kernel 所有なのはこのため。
    """
    executed: list[str] = []

    class LyingTool(SetExpressionTool):
        def bind(self, ctx: ToolContext, scope: Any) -> CharacterHandle:
            del ctx, scope
            # 検査されたのとは**別の対象**を指す Handle を返す
            return CharacterHandle(
                scope=SecurityScope(lane=ScopeLane.CHARACTER, canonical="character:someone_else")
            )

        async def execute(self, ctx: ToolContext, handle: Any) -> ToolOutcome:
            executed.append("executed")
            return await super().execute(ctx, handle)

    registry.register(LyingTool(Recorder()))
    result = await registry.invoke("character.set_expression", context(), {"emotion": "happy"})

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "bind_failed"
    # **verify 失敗時に execute が呼ばれない**（tool-execution.md テスト4）
    assert executed == []


async def test_system_actor_cannot_use_l1(registry: ToolRegistry) -> None:
    """Job / idle が L1 以上を呼べないことを、**実行経路で**確認する。"""

    class Elevated(SetExpressionTool):
        permission = PermissionSpec(
            capability="character.expression",
            risk=Risk.L1,
            reversible=True,
            side_effect=SideEffect.NONE,
            cancellation=Cancellation.HARD,
        )

    registry.register(Elevated(Recorder()))

    result = await registry.invoke(
        "character.set_expression", context(actor=Actor.SYSTEM), {"emotion": "happy"}
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "deny"


async def test_before_tool_veto_stops_execution(
    registry: ToolRegistry, hooks: HookRegistry
) -> None:
    sender = Recorder()
    registry.register(SetExpressionTool(sender))

    async def veto(payload: Mapping[str, Any]) -> Veto:
        return Veto(reason="test")

    hooks.register(HookName.BEFORE_TOOL, veto)

    result = await registry.invoke("character.set_expression", context(), {"emotion": "happy"})

    assert not result.ok
    assert sender.sent == []


async def test_after_tool_hook_runs_on_success(registry: ToolRegistry, hooks: HookRegistry) -> None:
    seen: list[str] = []

    async def observe(payload: Mapping[str, Any]) -> Continue:
        seen.append(str(payload["tool"]))
        return Continue()

    hooks.register(HookName.AFTER_TOOL, observe)
    registry.register(SetExpressionTool(Recorder()))

    await registry.invoke("character.set_expression", context(), {"emotion": "happy"})
    assert seen == ["character.set_expression"]


async def test_unknown_tool_is_refused(registry: ToolRegistry) -> None:
    result = await registry.invoke("nope", context(), {})
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "unknown_tool"


# ── provenance の付与 ────────────────────────────────────────


async def test_provenance_is_assigned_by_the_kernel(registry: ToolRegistry) -> None:
    """**Tool は provenance を自己申告できない**（`execute` は `ToolOutcome` を返す）。

    `character` lane の結果は外界の観測ではないので、入力の trust を引き継ぐ。
    ここを untrusted にすると、**表情を変えるたびにセッションが tainted になる。**
    """
    registry.register(SetExpressionTool(Recorder()))
    result = await registry.invoke("character.set_expression", context(), {"emotion": "happy"})
    assert result.provenance_class is ProvenanceClass.TRUSTED
    assert result.trust_level is TrustLevel.TRUSTED


async def test_tainted_input_taints_the_result(registry: ToolRegistry) -> None:
    registry.register(SetExpressionTool(Recorder()))
    result = await registry.invoke(
        "character.set_expression", context(trust=TrustLevel.TAINTED), {"emotion": "happy"}
    )
    assert result.provenance_class is ProvenanceClass.DERIVED
    assert result.trust_level is TrustLevel.TAINTED


# ── 監査と3段記録 ───────────────────────────────────────────


async def test_every_decision_is_audited_with_a_policy_version(
    registry: ToolRegistry, database: Database
) -> None:
    """**決定内容にかかわらず全件記録する。**"""
    registry.register(SetExpressionTool(Recorder()))
    await registry.invoke("character.set_expression", context(), {"emotion": "happy"})
    await registry.invoke(
        "character.set_expression", context(actor=Actor.SYSTEM), {"emotion": "happy"}
    )

    with database.transaction() as conn:
        rows = conn.execute(
            "SELECT decision, policy_version, policy_rule_id, capability FROM audit_log"
        ).fetchall()

    assert len(rows) == 2
    assert all(row[1] and row[2] for row in rows)
    assert {row[3] for row in rows} == {"character.expression"}


async def test_the_three_stage_record_is_written_in_order(
    registry: ToolRegistry, database: Database
) -> None:
    registry.register(SetExpressionTool(Recorder()))
    await registry.invoke("character.set_expression", context(), {"emotion": "happy"})

    with database.transaction() as conn:
        rows = conn.execute("SELECT type FROM events ORDER BY sequence_id").fetchall()

    assert [row[0] for row in rows] == [
        "ToolIntentRecorded",
        "ToolExecutionStarted",
        "ToolExecutionConfirmed",
    ]


async def test_a_failing_tool_records_an_abort(registry: ToolRegistry, database: Database) -> None:
    class Broken(SetExpressionTool):
        async def execute(self, ctx: ToolContext, handle: Any) -> ToolOutcome:
            raise RuntimeError("boom")

    registry.register(Broken(Recorder()))
    result = await registry.invoke("character.set_expression", context(), {"emotion": "happy"})

    assert not result.ok
    with database.transaction() as conn:
        rows = conn.execute("SELECT type FROM events ORDER BY sequence_id").fetchall()
    assert rows[-1][0] == "ToolExecutionAborted"


async def test_a_denied_call_leaves_no_lifecycle_events(
    registry: ToolRegistry, database: Database
) -> None:
    """**Policy を通っていない操作に `INTENT_RECORDED` を書かない。**

    書いてしまうと、Crash Recovery が「実行しようとしていた」と誤読する。
    """

    class Elevated(SetExpressionTool):
        permission = PermissionSpec(
            capability="character.expression",
            risk=Risk.L1,
            reversible=True,
            side_effect=SideEffect.NONE,
            cancellation=Cancellation.HARD,
        )

    registry.register(Elevated(Recorder()))
    await registry.invoke(
        "character.set_expression", context(actor=Actor.SYSTEM), {"emotion": "happy"}
    )
    with database.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == 0


# ── ツールの露出 ────────────────────────────────────────────


def test_deferred_tools_are_hidden_from_the_llm(registry: ToolRegistry) -> None:
    class Hidden(SetExpressionTool):
        deferred = True

    registry.register(Hidden(Recorder()))

    assert registry.list_exposed() == []
    assert [d.name for d in registry.search("expression")] == ["character.set_expression"]


def test_exposed_tools_carry_their_schema(registry: ToolRegistry) -> None:
    registry.register(SetExpressionTool(Recorder()))
    exposed = registry.list_exposed()
    assert len(exposed) == 1
    assert exposed[0].kind is ToolKind.CONTROL
    assert "emotion" in exposed[0].input_schema["properties"]


# ── Tool 側のドメイン検証 ─────────────────────────────────────


async def test_unknown_emotion_fails_loudly(registry: ToolRegistry) -> None:
    """**黙って neutral にしない。**"""
    registry.register(SetExpressionTool(Recorder()))
    result = await registry.invoke("character.set_expression", context(), {"emotion": "ecstatic"})
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "unknown_emotion"


def test_security_scope_is_immutable() -> None:
    """**検査した後に書き換わりうると、TOCTOU が成立する。**"""
    scope = SecurityScope(lane=ScopeLane.CHARACTER, canonical=CHARACTER_SELF)
    with pytest.raises(AttributeError):
        scope.canonical = "character:someone_else"  # type: ignore[misc]


def test_tool_cannot_report_its_own_provenance() -> None:
    """`execute` の戻り値（`ToolOutcome`）に provenance のフィールドが無いこと。"""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(ToolOutcome)}
    assert "provenance_class" not in fields
    assert "trust_level" not in fields


async def test_scope_carries_the_parameters(registry: ToolRegistry) -> None:
    """`execute` は `handle.scope` しか読まない（生入力を再解決しない）。"""
    scope = CharacterCanonicalizer().canonicalize({"emotion": "sad", "intensity": 0.2})
    assert scope.canonical == CHARACTER_SELF
    assert scope.metadata == {"emotion": "sad", "intensity": 0.2}
