# Contributing to Lumi

Thanks for looking. Before you write any code, one thing is worth knowing up front, because it is
unusual and it shapes everything else here:

> ## Design comes first, code comes second.
>
> `docs/` is the single source of truth. **If your change alters the design, the design document
> changes before the code does** — in the same pull request, not afterwards.

This is not ceremony. Lumi is an agent with a microphone, screen access, memory of your
conversations, and — from Phase 4 — the ability to operate your PC. The constraints that keep that
safe only work if they are written down somewhere more durable than a diff.

*Design documents are in Japanese. Code, comments, and commit messages are in English —
[see the language policy below](#language-policy).*

---

## Before you start

| | |
|---|---|
| 1 | Read **[docs/DESIGN.md](docs/DESIGN.md)** — the design constitution. Check its `rev.` and current phase first; the docs are newer than any summary of them. |
| 2 | Read **[.claude/rules/00-invariants.md](.claude/rules/00-invariants.md)** — the eight invariants, condensed. Full text in [docs/contracts/invariants.md](docs/contracts/invariants.md). |
| 3 | Check **[docs/roadmap.md](docs/roadmap.md)** for which phase the work belongs to. **Phases are not skipped.** |
| 4 | Read the detailed document for whatever you are touching (table below). |

### Which document covers what

| Changing | Read |
|---|---|
| Attention Arbiter / Activity / Job / barge-in | [contracts/state-machines.md](docs/contracts/state-machines.md), [architecture/agent.md](docs/architecture/agent.md) |
| Tool execution, permissions | [contracts/tool-execution.md](docs/contracts/tool-execution.md), [architecture/permission.md](docs/architecture/permission.md), [interfaces/tool.md](docs/interfaces/tool.md) |
| Trust levels, prompt-injection defenses | [contracts/provenance.md](docs/contracts/provenance.md) |
| Events, commands, hooks | [contracts/event-model.md](docs/contracts/event-model.md) |
| Memory | [architecture/memory.md](docs/architecture/memory.md), [interfaces/memory.md](docs/interfaces/memory.md) |
| Persistence, encryption, retention, deletion | [contracts/privacy.md](docs/contracts/privacy.md) |
| Audio, VAD, TTS, latency SLO | [architecture/audio.md](docs/architecture/audio.md) |
| Autonomous behavior | [architecture/autonomy.md](docs/architecture/autonomy.md) |
| World / Internal State | [architecture/world-state.md](docs/architecture/world-state.md) |
| Shell / Stage / Widget | [architecture/ui.md](docs/architecture/ui.md), [interfaces/shell.md](docs/interfaces/shell.md) |
| Extensions / Providers | [architecture/extension.md](docs/architecture/extension.md), [interfaces/provider.md](docs/interfaces/provider.md) |
| Licensing, distributables, credits | [licensing.md](docs/licensing.md), [ADR-019](docs/decisions/ADR-019-tts-engine-distribution.md) |
| Security boundaries | [contracts/security-boundaries.md](docs/contracts/security-boundaries.md) |

**When you are unsure who is allowed to do what, look at
[contracts/authority-matrix.md](docs/contracts/authority-matrix.md).** If what you are writing has
no ✓ in that table, it is a design violation, not a judgement call.

---

## Development setup

**Prerequisites** — Rust (MSVC toolchain) · Node 24+ · pnpm 11 ·
[uv](https://docs.astral.sh/uv/) (uv fetches Python 3.12 itself). Windows only for now.

```bash
git clone https://github.com/taka2360/Lumi.git
cd Lumi
pnpm install
cd core && uv sync && cd ..

pnpm dev            # launch the app (Shell + Stage, with Core as a sidecar)
pnpm stage:dev      # Stage alone
```

### Checks that must pass

CI runs three jobs — Core (Python), Shell (Rust), Stage (TypeScript). Run the relevant ones before
pushing:

```bash
# core/
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest

# shell/src-tauri/
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test

# stage/
pnpm lint && pnpm typecheck && pnpm test
```

**Never skip hooks with `--no-verify`.** If a hook fails, fix the cause.

---

## The workflow

### 1. Does this change the design?

If the answer is yes, or even "maybe":

```
you realize the design needs to change
  -> add an ADR under docs/decisions/
  -> update the affected docs/ files
  -> then write the code
```

How a change is treated depends on its confidence level in [docs/DESIGN.md](docs/DESIGN.md) §8:

| Level | Treatment |
|---|---|
| **Confirmed** | **Write a new ADR and survey the blast radius before changing anything.** Do not change it unilaterally. Everything in `contracts/` and all eight invariants are Confirmed. |
| Provisional | Ordinary design change — record it and update the docs. |
| Deferred | Not being decided at this stage. If you want to decide it, check which phase it belongs to first. |

### 2. Writing an ADR

`docs/decisions/ADR-NNN-<kebab-case-title>.md`, following the structure of the existing 46:

```
Status / Date / Related (table)
## Decision       What was decided, stated affirmatively
## Reason         Why. Include counterexamples and concrete failure cases
## Alternatives   Options considered and why they were not chosen — including their upsides
## Trade-offs     Costs accepted / what is gained
## Consequences   What else changes as a result
```

- **An ADR is a record of a decision at a point in time. It is not rewritten later.** If it needs
  to change, write a new ADR and add a note to the old one saying what superseded it.
- **Write down what you do not guarantee.** Overstated guarantees cause the next implementer to
  skip other defenses.

### 3. Branch and commit

Never work directly on `main`.

| Kind | Naming |
|---|---|
| Feature | `feat/p<phase>-<topic>` — e.g. `feat/p0-tauri-transparent-window` |
| Fix | `fix/<topic>` |
| Docs / ADR | `docs/<topic>` |
| Spike | `spike/<topic>` — **assumed to be thrown away, not merged** |

Commits are in English, and should make **why** the change was made clear — not just what changed.
One topic per commit. A change that touches an invariant does not get mixed with anything else.
If a change alters the design, **the ADR and doc updates belong in the same commit** — code does
not land first.

```text
Split the Kernel execution contract into Class A and Class B

Handles cannot cross process boundaries in out-of-process execution,
so BindVerifier cannot work. Move fs and computer in-core.

ADR-017
```

### 4. Open the pull request

Describe what changed, why, and which documents or ADRs it corresponds to. If it touches an
invariant, a boundary, or the permission path, say so explicitly — that changes how it is reviewed.

---

## What reviewers look for

- **Invariant violations.** These are not weighed against convenience. "It's just for debugging"
  and "we can call it directly here, it's faster" are violations, not trade-offs.
- **Code that disagrees with `docs/`.** Even when the code behaves fine, the disagreement is the
  defect. Either the code or the document is wrong; decide which, and fix that.
- **Abstractions that no design asked for.** Design principle 7: abstraction is justified by
  *probability it is actually needed × cost of changing later*. "We might want this someday" is
  not justification.
- **Anything that degrades silently.** Things that do not work must fail explicitly. When in
  doubt, fail closed.
- **Decisions handed to the LLM.** Judgement is deterministic code; generation is the LLM. "Should
  this happen?" is never an LLM's call.
- **Testability without an LLM.** If something can only be tested by calling a model, the design
  is wrong.

### Mistakes that are easy to make

- `trust_level = TRUSTED` may be written in exactly **two** places — the direct user-input handler,
  and `MemoryStore._confirm_in()` (reached from the memory UI's "confirm" and "correct" actions,
  per [ADR-043](docs/decisions/ADR-043-user-edited-memories-are-confirmed.md)).
- Only the **EventBus** assigns `sequence_id`.
- Only the **Attention Arbiter** performs Activity state transitions.
- Only the **Tool Registry** performs Tool state transitions.
- Exactly one function returns a `Decision`: `decide()`.
- At most one Activity is `running`.
- A Job's `actor` is always `system` (L0 only).

---

## Single source of truth

**Do not write the same thing in two places.** [docs/DESIGN.md](docs/DESIGN.md) §12 lists where
each thing is defined.

1. Put the table, formula, or type definition in its one defining location. Everywhere else links.
2. Restating the gist in one or two lines is fine. **Restating a table, formula, or code block
   is not.**
3. ADRs are exempt — they are point-in-time records, so duplication there is not updated.
4. Before adding a new table, formula, or type, check whether §12 needs a row for it.

---

## Language policy

**Documents, ADRs, and implementation plans are written in Japanese. Commit messages are English.**

Within the code, it depends on the audience:

| Target | Language | Why |
|---|---|---|
| Identifiers, comments, docstrings | **English** | This is read as open source |
| Internal exception messages, structured logs, assertion failures | **English** | Same reason — **developers read these, not users** |
| **User-facing text** | **Through `stage/src/i18n`** | The locale setting must affect everything. **The Core never sends display strings** ([ADR-036](docs/decisions/ADR-036-core-sends-reason-codes.md)); it sends stable reason codes, and the Stage owns the translations |
| LLM input (persona prompts, isolation-block formatting) | **Japanese** | Not display text. Being Japanese is meaningful here |

Putting a Japanese literal into `stage.*` means **that one line stops responding to the locale
setting.**

---

## Things we do not do

- Add an abstraction layer that no design calls for, on the grounds that it might be useful later
- Turn the Core into a plugin system. **Only things meant to be swapped or added become
  extensions.**
- Skip a phase. If a 🔴 "decide before starting" item in [docs/roadmap.md](docs/roadmap.md) is
  unresolved, that gets settled first
- Commit build outputs, model files, voice libraries, or Cubism Core
- **Put non-OSS material in the repository** — the Core's MIT boundary is not negotiable
- Commit anyone's memory database, audit log, or `CLAUDE.local.md`

---

## Reporting problems

- **Bugs and feature ideas** — open an issue. Include your OS, GPU, and which phase's
  functionality is involved.
- **Security vulnerabilities** — do not open a public issue. See [SECURITY.md](SECURITY.md).
- **Questions about the design** — an issue is fine. "Why is it built this way?" questions often
  surface documentation that is missing or wrong, which is useful.

Lumi is an early, opinionated, one-maintainer project. Large unsolicited pull requests are likely
to sit unmerged simply because reviewing them against the design takes longer than writing them
did. **Open an issue first for anything substantial** — it is the fastest path to a merge.

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
