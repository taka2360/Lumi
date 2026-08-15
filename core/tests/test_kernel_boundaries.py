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


def test_trust_level_trusted_is_only_written_where_allowed() -> None:
    """**自動昇格の実装を作らない**（Invariant 7 / provenance.md テスト5）。

    許されるのは2箇所だけ:
    1. ユーザーの直接入力を受け取るハンドラ（Phase 1 の Step E で登場する）
    2. 記憶 UI のユーザー確認ハンドラ（Phase 2）

    **現時点ではどちらも存在しないので、書き込みは1つも無いのが正しい。**
    増えたらこの allowlist を更新し、そのとき「本当にユーザー確認を経ているか」を見直す。
    """
    allowed: set[str] = set()
    writers = {
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "trust_level=TrustLevel.TRUSTED" in source.read_text(encoding="utf-8")
        or "trust_level = TrustLevel.TRUSTED" in source.read_text(encoding="utf-8")
    }
    assert writers == allowed
