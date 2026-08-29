"""Static checks. **These are tests too** (.claude/rules/tests.md).

Anything grep and AST can cover is enforced here instead of building runtime machinery.

| Check | Basis |
|---|---|
| `kernel/` depends on no other module | docs/architecture/core.md §4 |
| Only the Arbiter executes state transitions | docs/contracts/state-machines.md |
| Only the Arbiter assigns `_foreground` | .claude/rules/kernel.md |
| Only the EventBus constructs `DomainEvent` | docs/contracts/event-model.md test 6 |
| Where `trust_level = TRUSTED` is written | docs/contracts/provenance.md test 5 |
| No import cycles between packages | authority-matrix #20 / ADR-045 |
| `providers/` does not import `lumi.setup` | authority-matrix #21 / ADR-045 |
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
    """**The kernel holds only types and arbitration;
    it knows nothing about concrete capabilities.**

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


def package_of(source: Path) -> str:
    """The node this file belongs to: its package, or its own name if top-level."""
    relative = source.relative_to(LUMI).as_posix()
    head, _, tail = relative.partition("/")
    return head if tail else head.removesuffix(".py")


def package_graph() -> dict[str, set[str]]:
    """`lumi` package -> the packages it imports. Self-edges dropped."""
    edges: dict[str, set[str]] = {}
    for source in lumi_sources():
        importer = package_of(source)
        for name in imported_names(source):
            if not name.startswith("lumi."):
                continue
            imported = name.removeprefix("lumi.").split(".")[0]
            if imported and imported != importer:
                edges.setdefault(importer, set()).add(imported)
    return edges


def find_cycle(edges: dict[str, set[str]]) -> list[str]:
    """One cycle as a path, or `[]`. Depth-first; the first one found is enough."""
    visiting: list[str] = []
    done: set[str] = set()

    def walk(node: str) -> list[str]:
        if node in visiting:
            return [*visiting[visiting.index(node) :], node]
        if node in done:
            return []
        visiting.append(node)
        for target in sorted(edges.get(node, ())):
            if cycle := walk(target):
                return cycle
        visiting.pop()
        done.add(node)
        return []

    for start in sorted(edges):
        if cycle := walk(start):
            return cycle
    return []


def test_no_import_cycles_between_packages() -> None:
    """**No package under `lumi` may import another that imports it back**
    (authority-matrix #20 / ADR-045).

    `providers` and `setup` were mutually importing until ADR-045. It did not raise
    `ImportError` because the concrete modules happened not to meet — **the failure
    was one added import line away, and invisible to whoever added it.**

    A cycle is reported as a path so the offending edge is readable, not just present.
    """
    assert find_cycle(package_graph()) == []


def test_providers_do_not_import_setup() -> None:
    """**Direction, not merely acyclicity** (authority-matrix #21 / ADR-045).

    `artifacts <- providers <- setup`. A Provider is told where its model is; it does
    not know how the model got there. Checking only for cycles would accept "delete
    the `setup -> providers` edge instead", which reverses the layering while keeping
    the graph acyclic.

    The legal direction (`setup` importing `providers`) is deliberately not checked.
    """
    offenders = [
        f"{source.relative_to(LUMI).as_posix()}: {name}"
        for source in sorted((LUMI / "providers").rglob("*.py"))
        for name in sorted(imported_names(source))
        if name.startswith("lumi.setup")
    ]
    assert offenders == []


def test_only_the_arbiter_applies_state_transitions() -> None:
    """Only the Arbiter calls `Activity._apply` (defined in activity.py)."""
    callers = [
        source.relative_to(LUMI).as_posix()
        for source in lumi_sources()
        if "._apply(" in source.read_text(encoding="utf-8")
    ]
    assert callers == ["kernel/arbiter.py"]


def test_only_the_arbiter_assigns_the_foreground() -> None:
    """If assignments to `_foreground` scattered, Invariant 4 couldn't be upheld in one place."""
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
    """The dependency direction is `tools → permission`.
    **The Kernel knows nothing about individual tools** (Invariant 1).

    If it started knowing, the Permission Kernel would change every time a tool is added.
    """
    for source in sorted((LUMI / "permission").rglob("*.py")):
        assert not any(name.startswith("lumi.tools") for name in imported_names(source)), (
            source.name
        )


def test_tools_do_not_call_the_permission_kernel() -> None:
    """**If a Tool authorized itself, an implementation mistake would become
    a permission bypass directly** (Invariant 1).

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


#: The one file allowed to delete from the audit log (docs/contracts/privacy.md §5).
#:
#: **"Lumi cannot erase its own tracks" and "the user may erase their data" are different
#: claims**, and only the first one is the Invariant. The retention job and "erase
#: everything" are the user's own doing; every other route is Lumi's, and there is none.
AUDIT_DELETION_SITE = LUMI / "storage" / "retention.py"


def test_the_audit_log_is_append_only() -> None:
    """**Append-only means "no `DELETE` / `UPDATE` outside the deletion service."**

    Tampering from OS admin privileges can't be prevented. **That's outside the
    threat model, and this never claims otherwise.** **Detection** via a hash chain is Phase 4a.

    The exception is enumerated rather than assumed: privacy.md §5 reads static check 9 as
    "no `DELETE` / `UPDATE` on `audit_log` **outside the retention job and the erase
    service**", and both of those live in one file. **A second file appearing here fails**,
    which is the property that matters — not the count of deletions, but that they are all
    somewhere a reader can find in one look.
    """
    for source in lumi_sources():
        # **The exact path, not the basename.** A second `retention.py` somewhere else
        # under `lumi/` would otherwise inherit the exception by being named the same.
        if source == AUDIT_DELETION_SITE:
            continue
        text = source.read_text(encoding="utf-8").upper()
        assert "DELETE FROM AUDIT_LOG" not in text, source.name
        assert "UPDATE AUDIT_LOG" not in text, source.name


def test_only_the_deletion_service_deletes_the_audit_log() -> None:
    """★ The other half of the check above: **the exception is where it says it is.**

    Skipping a file by name is only safe while that file is the one documented to hold
    the exception. If `retention.py` ever stops deleting the audit log, the skip above
    becomes a hole nobody is watching — so it is an error for the exception to be unused.
    """
    text = AUDIT_DELETION_SITE.read_text(encoding="utf-8").upper()
    assert "DELETE FROM AUDIT_LOG" in text


def grants_trusted(path: Path) -> bool:
    """Whether this module hands `TrustLevel.TRUSTED` to anything as a value.

    **Not a substring search for `trust_level=`.** The first version of this check looked
    for that spelling, and would have missed `MemoryStore.confirm()` writing the level as
    a SQL parameter — an escalation path the check exists to enumerate, invisible to it
    because of how the call happened to be written.

    Comparisons are not grants: `if level is TrustLevel.TRUSTED` reads the value, and a
    check that flagged reads would be silenced by adding every module to the allow list.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    compared: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                compared.add(id(operand))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "TRUSTED"
            and isinstance(node.value, ast.Name)
            and node.value.id == "TrustLevel"
            and id(node) not in compared
        ):
            return True
    return False


def test_trust_level_trusted_is_only_written_where_allowed() -> None:
    """**No automatic-escalation implementation is ever built**
    (Invariant 7 / provenance.md test 5).

    Exactly two places grant it, plus the module that defines propagation:

    1. The handler that receives direct user input → `Session.record_user_utterance`
    2. The memory UI's user-confirmation handler → `MemoryStore.confirm` (Phase 2d)
    3. `provenance.py` itself, whose `join` / `taint` / `propagate` return it **only when
       every input was already trusted** — propagation, never escalation

    The Episode log is deliberately *not* on this list: it records the level the Session
    granted rather than deciding one, so that "who decides the user's input is trusted"
    has a single answer.

    **When adding one, re-examine whether it is genuinely an expression of the user's
    intent.** "Because it is an STT result" or "because it was summarized" are not
    valid reasons.
    """
    allowed = {"agent/session.py", "memory/store.py", "provenance.py"}
    granters = {
        source.relative_to(LUMI).as_posix() for source in lumi_sources() if grants_trusted(source)
    }
    assert granters == allowed
