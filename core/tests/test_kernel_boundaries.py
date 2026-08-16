"""Static checks. **These are tests too** (.claude/rules/tests.md).

Anything grep and AST can cover is enforced here instead of building runtime machinery.

| Check | Basis |
|---|---|
| `kernel/` depends on no other module | docs/architecture/core.md §4 |
| Only the Arbiter executes state transitions | docs/contracts/state-machines.md |
| Only the Arbiter assigns `_foreground` | .claude/rules/kernel.md |
| Only the EventBus constructs `DomainEvent` | docs/contracts/event-model.md test 6 |
| Where `trust_level = TRUSTED` is written | docs/contracts/provenance.md test 5 |
"""

from __future__ import annotations

import ast
from pathlib import Path

LUMI = Path(__file__).resolve().parents[1] / "lumi"
KERNEL = LUMI / "kernel"

#: Modules under lumi that `kernel/` may import (prefix match).
#: **When adding one, also update docs/architecture/core.md §4's exception table.**
KERNEL_ALLOWED_PREFIXES = ("lumi.kernel.", "lumi.provenance", "lumi.logging")


def lumi_sources() -> list[Path]:
    return sorted(LUMI.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    """Picks up `from lumi import logging` as `lumi.logging`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_kernel_does_not_depend_on_other_modules() -> None:
    """**The kernel holds only types and arbitration; it knows nothing about concrete capabilities.**

    "Outside world" concerns like persistence are received through a Protocol. The
    implementation lives outside the kernel.
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
    """The dependency direction is `storage → kernel`."""
    for source in sorted(KERNEL.rglob("*.py")):
        assert not any(name.startswith("lumi.storage") for name in imported_names(source)), (
            source.name
        )


def test_only_the_arbiter_applies_state_transitions() -> None:
    """Only the Arbiter calls `Activity._apply` (defined in activity.py)."""
    callers = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "._apply(" in source.read_text(encoding="utf-8")
    ]
    assert callers == ["kernel/arbiter.py"]


def test_only_the_arbiter_assigns_the_foreground() -> None:
    """If assignments to `_foreground` were scattered, Invariant 4 couldn't be upheld in one place."""
    writers = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "self._foreground = " in source.read_text(encoding="utf-8")
    ]
    assert writers == ["kernel/arbiter.py"]


def test_only_the_event_module_constructs_domain_events() -> None:
    """**Only the EventBus constructs a `DomainEvent` carrying a `sequence_id`.**

    A publisher can only construct a `DomainEventDraft` (blocked at the type level).
    This confirms there's exactly one place actually doing the constructing.
    """
    builders = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "DomainEvent(" in source.read_text(encoding="utf-8")
    ]
    assert builders == ["kernel/event.py"]


def test_permission_does_not_depend_on_tools() -> None:
    """The dependency direction is `tools → permission`. **The Kernel knows nothing about individual tools** (Invariant 1).

    If it started knowing, the Permission Kernel would change every time a tool is added.
    """
    for source in sorted((LUMI / "permission").rglob("*.py")):
        assert not any(name.startswith("lumi.tools") for name in imported_names(source)), (
            source.name
        )


def test_tools_do_not_call_the_permission_kernel() -> None:
    """**If a Tool authorized itself, an implementation mistake would become a permission bypass directly** (Invariant 1).

    A Tool may only import types (`PermissionSpec` / `SecurityScope`, etc); it never
    touches the decision-making side (`PermissionKernel` / `decide` / `GrantStore`).
    """
    forbidden = {
        "lumi.permission.kernel.PermissionKernel",
        "lumi.permission.policy.decide",
        "lumi.permission.policy.decide_with_rule",
        "lumi.permission.grants.GrantStore",
    }
    for source in sorted((LUMI / "tools").rglob("*.py")):
        if source.name == "registry.py":
            continue  # The Registry is the caller of the Kernel (the sole path)
        assert not (imported_names(source) & forbidden), source.name


def test_tools_do_not_implement_the_verifiers() -> None:
    """Implementing `canonicalize` / `verify` on the Tool side **would mean trusting the Tool.**"""
    for source in sorted((LUMI / "tools").rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        assert "def canonicalize(" not in text, source.name
        assert "def verify(" not in text, source.name


def test_only_the_registry_executes_tools() -> None:
    """`Tool.execute` is never called from anywhere but `ToolRegistry.invoke` (Invariant 2)."""
    callers = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "tool.execute(" in source.read_text(encoding="utf-8")
    ]
    assert callers == ["tools/registry.py"]


def test_only_decide_returns_a_decision() -> None:
    """**The only function returning a `Decision` is `decide()`** (.claude/rules/00-invariants.md).

    `decide_with_rule` returns `tuple[Decision, str]`, so it doesn't trip this check.
    Watches for a second "decision-making function" sprouting up, by return type.
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
    """**Phase 1's append-only means "no `DELETE` / `UPDATE` exists anywhere in the codebase."**

    Tampering from OS admin privileges can't be prevented. **That's outside the
    threat model, and this never claims otherwise.** **Detection** via a hash chain is Phase 4a.
    """
    for source in lumi_sources():
        text = source.read_text(encoding="utf-8").upper()
        assert "DELETE FROM AUDIT_LOG" not in text, source.name
        assert "UPDATE AUDIT_LOG" not in text, source.name


def test_trust_level_trusted_is_only_written_where_allowed() -> None:
    """**No automatic-escalation implementation is ever built** (Invariant 7 / provenance.md test 5).

    Exactly two places are allowed:
    1. The handler that receives direct user input → `Session.record_user_utterance`
    2. The memory UI's user-confirmation handler (**Phase 2. Doesn't exist yet**)

    **When adding one, re-examine whether it's genuinely an expression of the user's
    intent.** "Because it's an STT result" or "because it was summarized" are not
    valid reasons.
    """
    allowed = {"agent/session.py"}
    writers = {
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "trust_level=TrustLevel.TRUSTED" in source.read_text(encoding="utf-8")
        or "trust_level = TrustLevel.TRUSTED" in source.read_text(encoding="utf-8")
    }
    assert writers == allowed
