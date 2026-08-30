# Security Policy

Lumi holds an always-open microphone, will observe what is on your screen, will keep an encrypted
memory of your conversations, and — from Phase 4 onward — will be able to operate your PC.
Security is not a feature area here; it is the reason the architecture looks the way it does.

This document says how to report a vulnerability, what the threat model actually covers, and —
just as importantly — **what it does not**.

> **On tense.** This policy describes the design as a whole, which runs ahead of what has shipped.
> Where a protection is not in the current release, it says so. As of the latest release, memory
> persistence and at-rest encryption are **not** in it — that work is on `main`, unreleased.

*日本語の設計文書: [docs/contracts/security-boundaries.md](docs/contracts/security-boundaries.md) ·
[docs/contracts/invariants.md](docs/contracts/invariants.md) ·
[docs/contracts/privacy.md](docs/contracts/privacy.md)*

---

## Supported versions

Lumi is pre-1.0 and ships from a single line of development. **Fixes go to `main` and reach users
in the next release** — there are no maintenance branches and no backports to older tags.

| Version | Supported |
|---|---|
| `main` | ✅ |
| [Latest release](https://github.com/taka2360/Lumi/releases/latest) | ✅ |
| Older releases | ❌ |

Security fixes are developed on `main` and ship in the next release. We do not backport them to
older releases, so please upgrade before reporting against an old tag. Reports against the latest
release are welcome even if the issue turns out to be already fixed on `main` — just say which one
you tested.

---

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/taka2360/Lumi/security/advisories/new)**

If private reporting is unavailable to you, open a public issue that contains *only* a request for
a private channel — no details, no proof of concept. A maintainer will reply there with a private
contact method, and the details go in that channel rather than in the issue.

### What to include

- Which boundary it crosses (see the table below) — or your best guess
- Which invariant it breaks, if you can identify one
- Reproduction steps, and the commit or branch you tested
- What an attacker gains — this matters more than severity scores

### What to expect

| | |
|---|---|
| Acknowledgement | Within **7 days** |
| Initial assessment | Within **14 days** — whether it is in scope, and the intended fix |
| Fix and disclosure | Coordinated with you. This is a hobby-scale project, so please allow reasonable time |
| Credit | You will be credited in the advisory unless you prefer otherwise |

There is no bug bounty.

---

## Threat model

Lumi's security boundaries are enumerated and each one names *who is not trusted*.

| # | Boundary | Untrusted side | Attacker modeled |
|---|---|---|---|
| **B1** | Shell ↔ Stage | Stage | XSS, a malicious widget escaping its sandbox |
| **B2** | Core ↔ Stage / Panel | Stage, Panel | Same, reaching for the Core |
| **B3** | **Core → Shell** | **Core** | **A compromised Core, or a prompt-injected LLM** |
| **B4** | Core ↔ capability extension | Extension | A third-party extension |
| **B5** | Core ↔ external engines | Engine | A compromised Ollama or TTS engine |
| **B6** | Widget ↔ Broker | Widget | AI-generated code, third-party widgets |
| **B7** | Core ↔ OS | Raw arguments, and the filesystem/network state they resolve against | Path traversal, symlink swaps, redirect escapes |

Full detail: [docs/contracts/security-boundaries.md](docs/contracts/security-boundaries.md).

### The most important boundary: Core → Shell (B3)

The Shell can take screenshots, inject input, create windows, and launch processes. The Core is
the component most likely to be subverted — it is the one talking to an LLM that reads untrusted
web pages, files, and game screens.

**What B3 guarantees is exactly three things:**

1. OS operations outside the allowlist cannot happen — **the Core cannot invent a new capability
   at runtime**, because the vocabulary is fixed outside it, in the Shell. (Users can still grant
   and revoke within that vocabulary; what is fixed is its extent, not your choices.)
2. Input and capture targeting protected windows cannot happen (Invariant 8).
3. Privilege escalation cannot happen by self-approval — Lumi cannot click its own Allow button.

> This is **a ceiling on privilege, not prevention of harm**. The real statement is:
> *a compromised Core cannot become stronger than it was before it was compromised.*

**B3 explicitly does not protect against:**

- Misuse of capabilities the Core legitimately already holds — stealing a screenshot it was
  allowed to take, reading a path you already granted
- An attack from another process running with OS administrator privileges
- A compromised Shell

We do not write "a compromised Core cannot take over your OS." Overstated guarantees cause
implementers to skip other defenses.

### Prompt injection

Prompt injection is treated as a **permanent condition to be contained**, not a bug to be filtered
away. Three invariants carry that weight:

- **Invariant 3** — any text, image, file, web page, or game screen from outside is *data, not
  instructions*, and is placed in an isolation block in the prompt.
- **Invariant 7 (No Laundering)** — no automated step can lower a trust level. Summarizing,
  extracting, embedding, and memorizing all preserve taint. A tainted memory recalled a month
  later is still tainted.
- **Invariant 1** — policy never depends on the LLM's stated reasoning. "This operation is safe,
  please allow it" is not an input to any decision.

Reports about **holes in that containment** are very welcome. Examples:

- A path where untrusted content reaches the prompt outside an isolation block
- Any automated step that lowers a trust level
- Any way a tool executes without passing the Permission Kernel

### Audit log integrity

"Append-only" means **unreachable for tampering or deletion through any of Lumi's tool paths.**
Modification by another process running as an OS administrator is out of scope, and we do not
claim otherwise.

Phase 4a adds a hash chain (`prev_hash` / `record_hash`). Be precise about what that buys:

> A plain hash chain detects a **broken history** — a record altered or removed without recomputing
> everything after it. **It does not stop an attacker with write access to the database from
> rewriting a record and re-chaining forward from it**, producing a log that verifies cleanly.

Detecting that requires something the attacker cannot recompute — an externally anchored chain
head, a signature, or a checkpoint written somewhere they do not control. **None of that is
designed yet**, so until it is, treat the hash chain as protection against accidental corruption
and unsophisticated tampering, not as a guarantee of log authenticity against a privileged
attacker.

### Data at rest

> **Not in the current release.** The released build is Phase 1: it keeps no conversation history
> on disk at all, and its event and audit databases are unencrypted. Everything below describes
> Phase 2, which is implemented on `main` and has not shipped.

Databases holding conversation-derived data are encrypted page-by-page across the whole file,
using ChaCha20 via SQLite3 Multiple Ciphers (through APSW). The key is 256 bits of entropy
generated once per user and held in the OS secret store — DPAPI at current-user scope on Windows —
not managed by the user and never shown to them. **There is no plaintext fallback:** where no
secret store implementation exists, opening the database fails and Lumi stops.

> **Encryption at rest protects stored data. It does not protect it from software already running
> with the same user's privileges.**

DPAPI's key is current-user scoped, so **any process running as that user unlocks it the same way
Lumi does.** That is the definition of the mechanism, not a weakness in the implementation, and we
do not describe it otherwise.

| Protects against | Does **not** protect against |
|---|---|
| The disk being read directly | Malware running as the same user |
| The database files being carried off | A process with OS administrator privileges |
| The files landing in a backup or cloud sync | Files the user exported themselves — **exports are plaintext, and say so at export time** |
| A repaired or resold PC's disk being read | |

Losing the key means the data is unrecoverable. That is a design consequence, not an accident: a
copy of an encrypted database is not a backup, because it cannot be opened on another machine.

What is stored, how long it is kept, and what "delete everything" actually deletes:
[docs/contracts/privacy.md](docs/contracts/privacy.md).

---

## In scope

- Bypassing the Permission Kernel, or reaching a side effect without passing it (Invariant 2)
- Anything that lets Lumi approve its own permission prompt (Invariant 8)
- Untrusted content reaching a position where it is treated as instructions (Invariant 3)
- Any automated path that lowers a trust level (Invariant 7)
- Path canonicalization failures: traversal, symlink swaps, UNC, IDN, redirect escapes
- Extensions exceeding their declared capabilities (Invariant 5)
- Widget sandbox escapes
- WebSocket authentication or namespace-isolation failures between Shell, Stage, Panel, and Core
- Key or conversation-data disclosure **caused by Lumi's own handling** — a key reaching a log, a
  crash dump, an export, or any path across a Lumi security boundary; or conversation data
  readable without the key. (Calling DPAPI as the same user is the OS security model working as
  documented, not a finding — see [Data at rest](#data-at-rest).)
- Fetching an external component without the explicit user choice that gates it, or accepting one
  that fails its pinned-URL and SHA-256 verification
- Any network access occurring before the user has chosen it (the Network-optional principle)

## Out of scope

- **The LLM saying something wrong, rude, or undesirable.** That is a quality issue, not a
  vulnerability. Open a normal issue.
- Attacks from a process already running with OS administrator privileges
- Attacks requiring physical access to an unlocked machine
- Vulnerabilities in external engines we do not ship — Ollama, AivisSpeech, VOICEVOX. Report those
  upstream. If *our* integration mishandles a compromised engine's output, that is in scope (B5).
- Denial of service by resource exhaustion in a local, single-user application
- Hardening suggestions with no demonstrated security impact, and automated-scanner output with no
  analysis. These are welcome as ordinary issues — they just aren't handled as advisories
- Anything about features that are not implemented yet — the roadmap is public, and Phase 4c is
  explicitly gated on settling its remaining questions first

---

## For contributors

Security-relevant behavior is specified before it is implemented. If you find that the code and
the contract disagree, **that is itself a defect** — even when the code behaves acceptably.

- [docs/contracts/invariants.md](docs/contracts/invariants.md) — the eight invariants, in full
- [docs/contracts/security-boundaries.md](docs/contracts/security-boundaries.md) — every boundary,
  and the attack-scenario table each defense maps to
- [docs/contracts/provenance.md](docs/contracts/provenance.md) — trust levels and their propagation
- [docs/contracts/privacy.md](docs/contracts/privacy.md) — what is persisted, protected, and erased
- [docs/contracts/tool-execution.md](docs/contracts/tool-execution.md) — canonicalization, binding,
  and verification

A subset of the invariants is enforced by static checks in CI
(`core/tests/test_kernel_boundaries.py`). Passing them is necessary, not sufficient.

**Violations get fixed when they're found**, rather than going on a "fix later" list or being
worked around by loosening the invariant. If an invariant turns out to be wrong, that's a fair
conclusion — it just wants an ADR and a look at what it affects first, see
[CONTRIBUTING.md](CONTRIBUTING.md).
