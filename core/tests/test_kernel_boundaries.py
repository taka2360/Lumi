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
| Background tasks are started through `spawn` | lumi/tasks.py |
| Who writes `memories` | authority-matrix #22 / ADR-045 |
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

LUMI = Path(__file__).resolve().parents[1] / "lumi"
KERNEL = LUMI / "kernel"

#: Modules under lumi that `kernel/` may import (prefix match).
#: **When adding one, also update docs/architecture/core.md §4's exception table.**
#: Each is groundwork rather than a capability: types every module needs
#: (`provenance`), or the plumbing every module runs on (`logging`, `tasks`).
KERNEL_ALLOWED_PREFIXES = ("lumi.kernel.", "lumi.provenance", "lumi.logging", "lumi.tasks")


def lumi_sources() -> list[Path]:
    return sorted(LUMI.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    """Picks up `from lumi import logging` as `lumi.logging`.

    **Relative imports are resolved to absolute ones.** Skipping them would let
    `from ..setup import models` slip past every check here — and the shorter spelling is
    exactly the one someone reaches for when the import is one they half know is wrong.
    """
    package = ".".join(("lumi", *path.relative_to(LUMI).parts[:-1]))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # `from . import x` is level 1 and stays in `package`; each extra dot
                # climbs one more.
                base = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            elif node.module:
                module = node.module
            else:  # pragma: no cover - `from import` does not parse
                continue
            names.update(f"{module}.{alias.name}" for alias in node.names)
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


#: Every way to detach a coroutine from its caller. **`asyncio.create_task` is not the
#: only one** — `get_running_loop().create_task` and `ensure_future` do the same thing
#: with the same two problems, and a rule that names one spelling teaches the others.
TASK_STARTERS = ("create_task", "ensure_future")


def task_starts(path: Path) -> list[int]:
    """Lines calling something that starts a task. **Parsed, not grepped** — a mention in
    a docstring is not a call, and `loop.create_task` is.

    Importing one counts as starting one. `from asyncio import create_task as go` renames
    the call out of reach of any check that reads call sites, and the import is the one
    spelling the rename cannot hide.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr in TASK_STARTERS)
            or (isinstance(node.func, ast.Name) and node.func.id in TASK_STARTERS)
        )
    ]
    lines += [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "asyncio"
        and any(alias.name in TASK_STARTERS for alias in node.names)
    ]
    return sorted(lines)


#: Files that may call `asyncio.create_task` directly, and why.
#: **When adding one, write down who claims the task's result.** Everywhere else goes
#: through `lumi.tasks.spawn`, which keeps a strong reference and reports failures.
ALLOWED_DIRECT_CREATE_TASK: dict[str, str] = {
    "tasks.py": "Defines spawn(). Someone has to make the call",
    "agent/stt.py": (
        "The result is claimed — by the awaiting caller, or by _finished. A reporter "
        "here would log failures the consumer already handles"
    ),
}


def test_background_tasks_are_started_through_spawn() -> None:
    """**A task nobody holds can be collected mid-flight, and its exception surfaces at
    GC time if ever.**

    That is not a hypothetical: a missing `arbiter.start()` stayed invisible for a day
    (2026-08-17) because the reactive loop died into silence. `spawn` fixes both halves
    at once, which only helps if there is no second way to start a task.

    Files are listed with a reason rather than the rule being dropped — the exceptions
    are real, and the reason is what makes the next one arguable.
    """
    offenders = sorted(
        f"{source.relative_to(LUMI).as_posix()}:{line}"
        for source in lumi_sources()
        if source.relative_to(LUMI).as_posix() not in ALLOWED_DIRECT_CREATE_TASK
        for line in task_starts(source)
    )
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

#: The only two files that may write the `memories` table (ADR-045). One believes
#: things and one forgets them, **and forgetting is deletion**, which privacy.md §5
#: keeps in one file with every other `DELETE` against user data.
MEMORY_WRITERS = {
    LUMI / "memory" / "store.py": ("INSERT", "UPDATE"),
    LUMI / "storage" / "retention.py": ("UPDATE", "DELETE"),
}


def _literal_sql(node: ast.expr) -> str | None:
    """Literal SQL passed to ``execute``; interpolated values become placeholders."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "?"
            for part in node.values
        )
    return None


def _executed_sql(source: Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    statements: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
        ):
            continue
        statement = _literal_sql(node.args[0])
        if statement is not None:
            statements.append(statement)
    return statements


def _write_target(statement: str) -> tuple[str, str] | None:
    """The normalized write operation and table, if this is a supported SQL write."""
    normalized = re.sub(r"\s+", " ", statement).strip().upper()
    match = re.match(
        r"^(?P<operation>INSERT(?: OR REPLACE)?|REPLACE) INTO (?P<insert_table>[^\s(]+)"
        r"|^(?P<update>UPDATE) (?P<update_table>[^\s(]+)"
        r"|^(?P<delete>DELETE) FROM (?P<delete_table>[^\s(]+)",
        normalized,
    )
    if match is None:
        return None
    operation = match.group("operation") or match.group("update") or match.group("delete")
    table = (
        match.group("insert_table") or match.group("update_table") or match.group("delete_table")
    )
    # INSERT OR REPLACE and REPLACE have the same authority boundary as INSERT.
    canonical_operation = "INSERT" if operation in {"INSERT OR REPLACE", "REPLACE"} else operation
    return canonical_operation, table.strip('`"[]')


def _memory_writes(source: Path) -> set[str]:
    return {
        operation
        for statement in _executed_sql(source)
        if (target := _write_target(statement)) is not None
        for operation, table in (target,)
        if table == "MEMORIES"
    }


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


def test_only_the_store_and_the_purge_write_memories() -> None:
    """**One writer for what Lumi believes** (ADR-045 / authority-matrix #22).

    `MemoryStore` is where every rule about a belief is enforced: `user_confirmed` refused,
    evidence checked, trust joined with the utterances it came from, supersession writing
    the contradiction note in the same transaction. **A second writer would not be a
    shortcut past one of those, it would be past all of them** — and the symptom is a
    memory that reads fine and was never checked.

    The reads are deliberately not restricted. `memory/rows.py` and `memory/browse.py`
    both `SELECT`, and that is the point of them: a query builder that could also write
    would make this check unable to tell the two apart.

    Physical deletion is `storage/retention.py`, with every other `DELETE` against user
    data, because privacy.md §5 has to be checkable by reading one file.
    """
    for source in lumi_sources():
        writes = _memory_writes(source)
        allowed = set(MEMORY_WRITERS.get(source, ()))
        assert writes <= allowed, (
            f"{source.relative_to(LUMI).as_posix()}: "
            f"unauthorized {', '.join(sorted(writes - allowed))} on MEMORIES"
        )


def test_memory_write_parser_covers_sqlite_write_forms() -> None:
    """Whitespace and SQLite replacement syntax must not create a boundary-test escape."""
    statements = {
        "INSERT": "insert\ninto memories (id) values (?)",
        "INSERT OR REPLACE": "INSERT OR REPLACE INTO `memories` (id) VALUES (?)",
        "REPLACE": 'REPLACE INTO "memories" (id) VALUES (?)',
        "UPDATE": " update memories set archived_at = ? ",
        "DELETE": "DELETE\nFROM [memories] WHERE id = ?",
    }

    for statement in statements.values():
        assert _write_target(statement) in {
            ("INSERT", "MEMORIES"),
            ("UPDATE", "MEMORIES"),
            ("DELETE", "MEMORIES"),
        }


def test_both_memory_writers_still_write() -> None:
    """★ The other half: **an exception that stopped being used is a hole nobody watches.**

    Skipping a file is only safe while it is the file documented to hold the exception. If
    the purge stopped deleting memories, the skip above would quietly license a `DELETE`
    anywhere in `storage/retention.py`.
    """
    for source, operations in MEMORY_WRITERS.items():
        writes = _memory_writes(source)
        for operation in operations:
            assert operation in writes, (
                f"{source.relative_to(LUMI).as_posix()}: {operation} MEMORIES"
            )
