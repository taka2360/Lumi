"""静的検査。**これもテストである**（.claude/rules/tests.md）。

grep と AST で足りるものは、実行時の仕組みを作らずにここで縛る。

| 検査 | 根拠 |
|---|---|
| `kernel/` が他モジュールに依存しない | docs/architecture/core.md §4 |
| 状態遷移を実行するのは Arbiter だけ | docs/contracts/state-machines.md |
| `_foreground` への代入は Arbiter だけ | .claude/rules/kernel.md |
| `DomainEvent` を作るのは EventBus だけ | docs/contracts/event-model.md テスト6 |
| `trust_level = TRUSTED` の書き込み箇所 | docs/contracts/provenance.md テスト5 |
"""

from __future__ import annotations

import ast
from pathlib import Path

LUMI = Path(__file__).resolve().parents[1] / "lumi"
KERNEL = LUMI / "kernel"

#: `kernel/` が import してよい lumi 配下のモジュール（前方一致）。
#: **増やすときは docs/architecture/core.md §4 の例外表も直す。**
KERNEL_ALLOWED_PREFIXES = ("lumi.kernel.", "lumi.provenance", "lumi.logging")


def lumi_sources() -> list[Path]:
    return sorted(LUMI.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    """`from lumi import logging` を `lumi.logging` として拾う。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_kernel_does_not_depend_on_other_modules() -> None:
    """**kernel は型と調停だけを持ち、具体的な能力を知らない。**

    永続化のような「外の世界」は Protocol で受け取る。実装は kernel の外にある。
    """
    offenders: list[str] = []
    for source in sorted(KERNEL.rglob("*.py")):
        for name in imported_names(source):
            if not name.startswith("lumi"):
                continue
            if not name.startswith(KERNEL_ALLOWED_PREFIXES):
                offenders.append(f"{source.name}: {name}")
    assert offenders == []


def test_storage_may_depend_on_kernel_but_not_the_reverse() -> None:
    """依存の向きは `storage → kernel`。"""
    for source in sorted(KERNEL.rglob("*.py")):
        assert not any(name.startswith("lumi.storage") for name in imported_names(source)), (
            source.name
        )


def test_only_the_arbiter_applies_state_transitions() -> None:
    """`Activity._apply` を呼ぶのは Arbiter だけ（定義は activity.py）。"""
    callers = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "._apply(" in source.read_text(encoding="utf-8")
    ]
    assert callers == ["kernel/arbiter.py"]


def test_only_the_arbiter_assigns_the_foreground() -> None:
    """`_foreground` への代入が散ると、Invariant 4 を1箇所で守れなくなる。"""
    writers = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "self._foreground = " in source.read_text(encoding="utf-8")
    ]
    assert writers == ["kernel/arbiter.py"]


def test_only_the_event_module_constructs_domain_events() -> None:
    """**`sequence_id` を持つ `DomainEvent` を組み立てるのは EventBus だけ。**

    発行者は `DomainEventDraft` しか作れない（型で塞いである）。
    ここでは「実際に組み立てている場所」が1つであることを確かめる。
    """
    builders = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "DomainEvent(" in source.read_text(encoding="utf-8")
    ]
    assert builders == ["kernel/event.py"]


def test_permission_does_not_depend_on_tools() -> None:
    """依存の向きは `tools → permission`。**Kernel が個別ツールを知らない**（Invariant 1）。

    知り始めると、ツールを1つ足すたびに Permission Kernel が変わる。
    """
    for source in sorted((LUMI / "permission").rglob("*.py")):
        assert not any(name.startswith("lumi.tools") for name in imported_names(source)), (
            source.name
        )


def test_tools_do_not_call_the_permission_kernel() -> None:
    """**Tool が自分で authorize すると、実装ミスがそのまま権限バイパスになる**（Invariant 1）。

    Tool が import してよいのは型（`PermissionSpec` / `SecurityScope` など）だけで、
    判断する側（`PermissionKernel` / `decide` / `GrantStore`）には触れない。
    """
    forbidden = {
        "lumi.permission.kernel.PermissionKernel",
        "lumi.permission.policy.decide",
        "lumi.permission.policy.decide_with_rule",
        "lumi.permission.grants.GrantStore",
    }
    for source in sorted((LUMI / "tools").rglob("*.py")):
        if source.name == "registry.py":
            continue  # Registry は Kernel を呼ぶ側（そこが唯一の経路）
        assert not (imported_names(source) & forbidden), source.name


def test_tools_do_not_implement_the_verifiers() -> None:
    """`canonicalize` / `verify` を Tool 側に実装すると、**Tool を信頼することになる。**"""
    for source in sorted((LUMI / "tools").rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        assert "def canonicalize(" not in text, source.name
        assert "def verify(" not in text, source.name


def test_only_the_registry_executes_tools() -> None:
    """`ToolRegistry.invoke` 以外から `Tool.execute` を呼ばない（Invariant 2）。"""
    callers = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "tool.execute(" in source.read_text(encoding="utf-8")
    ]
    assert callers == ["tools/registry.py"]


def test_only_decide_returns_a_decision() -> None:
    """**`Decision` を返す関数は `decide()` ひとつだけ**（.claude/rules/00-invariants.md）。

    `decide_with_rule` は `tuple[Decision, str]` を返すので、この検査には掛からない。
    2つ目の「判断する関数」が生えていないことを、戻り値の型で見張る。
    """
    offenders: list[str] = []
    for source in lumi_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            returns = node.returns
            if isinstance(returns, ast.Name) and returns.id == "Decision":
                offenders.append(f"{source.name}:{node.name}")
    assert offenders == ["policy.py:decide"]


def test_the_audit_log_is_append_only() -> None:
    """**Phase 1 の append-only は「コードベースに `DELETE` / `UPDATE` が無い」こと。**

    OS 管理者権限からの改竄は防げない。**それは脅威モデル外であり、防げると書かない。**
    hash chain による**検出**は Phase 4a。
    """
    for source in lumi_sources():
        text = source.read_text(encoding="utf-8").upper()
        assert "DELETE FROM AUDIT_LOG" not in text, source.name
        assert "UPDATE AUDIT_LOG" not in text, source.name


def test_trust_level_trusted_is_only_written_where_allowed() -> None:
    """**自動昇格の実装を作らない**（Invariant 7 / provenance.md テスト5）。

    許されるのは2箇所だけ:
    1. ユーザーの直接入力を受け取るハンドラ → `Session.record_user_utterance`
    2. 記憶 UI のユーザー確認ハンドラ（**Phase 2。まだ存在しない**）

    **増やすときは、そこが本当にユーザーの意思表示かを見直すこと。**
    「STT の結果だから」「要約したから」は理由にならない。
    """
    allowed = {"agent/session.py"}
    writers = {
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "trust_level=TrustLevel.TRUSTED" in source.read_text(encoding="utf-8")
        or "trust_level = TrustLevel.TRUSTED" in source.read_text(encoding="utf-8")
    }
    assert writers == allowed
