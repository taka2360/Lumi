<p align="center">
  <img src="assets/branding/banner.png" alt="Lumi" width="100%">
</p>

<h1 align="center">Lumi</h1>

<p align="center">
  <strong>An AI life form that lives in your PC — not a chatbot in a window.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://github.com/taka2360/Lumi/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/taka2360/Lumi"></a>
  <img alt="Status: Phase 2" src="https://img.shields.io/badge/in%20development-Phase%202%20(Memory)-orange.svg">
  <img alt="Platform: Windows" src="https://img.shields.io/badge/platform-Windows-lightgrey.svg">
  <img alt="Local inference" src="https://img.shields.io/badge/inference-100%25%20local-brightgreen.svg">
</p>

<p align="center">
  <strong>English</strong> ·
  <a href="README.ja.md">日本語</a>
</p>

> [!WARNING]
> **Lumi is early software under active development.** Windows only, expect rough edges
> and breaking changes.
>
> The **[latest release](https://github.com/taka2360/Lumi/releases/latest)** is a Phase 1
> build — it talks, listens, and can be interrupted mid-sentence, but it does **not** yet
> remember anything between sessions. The Phase 2 memory work is on `main` and has not
> been released. See [Install](#install) and [Current progress](#current-progress--roadmap).

---

## What is Lumi?

Lumi is a desktop companion in the lineage of **[Ukagaka](https://en.wikipedia.org/wiki/Ukagaka)** —
a character that lives on your desktop rather than inside an app window — rebuilt on a modern,
fully local AI stack (LLM / STT / TTS / Vision / vector memory).

The goal is not "a chat UI with an avatar attached." It is something that *lives* in your PC:
it hears you, remembers you, notices what you are doing, and occasionally has something of its
own to say — without becoming annoying, and without ever taking a dangerous action behind your back.

<!-- TODO: a 5-15s screen capture belongs here — ideally showing barge-in: Lumi talking,
     the user cutting in, and playback stopping mid-word. Save it as
     assets/branding/demo.gif and reference it with:
     <p align="center"><img src="assets/branding/demo.gif" alt="Lumi in use" width="100%"></p> -->

### Three things Lumi is actually about

|  |  |  |
|---|---|---|
| 🗣️ | **Real barge-in** | You can cut Lumi off mid-sentence. Playback mutes inside the audio path, not after the next LLM token. The entire barge-in critical path is deliberately kept inside a single process. |
| 🧠 | **Memory** | Lumi remembers, forgets, and can hold a contradiction — "you told me the opposite last week." Episodes are persisted, decayed by salience, and searched with hybrid vector + full-text retrieval. |
| 🛡️ | **Safe autonomy** | Lumi acts on its own, but every side effect passes a deterministic permission kernel, and it can never click its own consent dialog. |

### What "local" means here

|  | Definition | Lumi |
|---|---|---|
| Air-gapped | Never touches the network | ✗ |
| Local-first | Core is local, external is supplementary | — |
| **Network-optional** | **External communication is optional and explicit** | **✓** |

> **Inference, state, and decisions are all local.** Network access happens only when it has been
> explicitly granted as a capability. A cloud LLM, if one ever exists, is not "the core moving to
> the cloud" — it is *an LLM provider that was handed the network capability*, and it is described,
> gated, and audited as exactly that.

In practice: **once Lumi is set up, a conversation involves no network at all** — speech
recognition, the language model, and speech synthesis all run on your machine. Setup itself is the
one point where anything is fetched, and **Lumi never fetches an optional component without asking
first.** It presents "fetch" and "don't fetch" as equally valid choices, and it never reaches the
network before you have chosen.

("Network-optional" is a defined term here, not a general claim — see
[DESIGN.md](docs/DESIGN.md) §1.)

---

## How it is built

Lumi is three processes with sharply separated authority.

```
+------------------ Lumi Shell (Tauri 2 / Rust) --------------------+
|  OS privileged primitives only. Holds no judgement.               |
|  transparency / always-on-top / click-through / hit-testing       |
|  tray - hotkeys - screen capture - input injection                |
|  launches and supervises the Core sidecar                         |
|  validates every os.* request from Core (auth + allowlist+schema) |
|                                                                   |
|   +------ Stage WebView (React + TS + Zustand) ------+            |
|   |  Presentation only. Holds no business logic.     |            |
|   |  VRM rendering - expression - lip-sync - bubbles |            |
|   +--------------------------------------------------+            |
+-------------------------------------------------------------------+
                    | WebSocket (token-authenticated)
+------------------ Lumi Core (Python / asyncio) --------------------+
|  THE AUTHORITY: decisions, state, policy, memory. Single process. |
|  Attention Arbiter - Reactive Loop - Deliberative Loop            |
|  Memory - World State - Internal State                            |
|  Permission Kernel - Tool Registry - Event Bus                    |
|  Audio I/O (capture / VAD / playback / EchoGuard)                 |
+-------------------------------------------------------------------+
       | ext.* (capability-gated)     external, not owned:
       v                              Ollama - AivisSpeech / VOICEVOX
   Sensor / Browser / GameAgent extensions
```

**The Core is the hub.** Shell, Stage, and every extension are its clients.
The Core holds *authority* — what should happen, what is allowed, what is remembered.
It deliberately holds **no tool capabilities**: no browser, no filesystem, no input injection, no
vision model. Those live behind the Permission Kernel, in the Shell, or in a separate process.

Audio is the deliberate exception, and the reason is barge-in: capture, VAD, playback, and echo
guarding sit *inside* the Core so that "stop talking" is decided in the audio path, without a
process hop. A boundary crossing there would be measured in the tens of milliseconds you can hear.

The test for what belongs in the Core is: *"remove this, and is it still Lumi?"* Remove the browser
and it is. Remove memory or the Attention Arbiter and it isn't.

### The eight invariants

These are constraints, not features. They may not be broken at any stage of implementation.

| # | Name | Rule |
|---|---|---|
| 1 | **Authority** | Only the Core Kernel decides permissions. Not the LLM, not the Stage, not the Shell, not extensions. |
| 2 | **Tool Gate** | Every operation with a side effect goes through the Permission Kernel. No bypass path is ever built. |
| 3 | **Untrusted Data** | External text, images, files, web content, and game screens are **data, not instructions**. |
| 4 | **Attention** | Exactly one Activity is in the foreground, always. Interruption goes through the Attention Arbiter. |
| 5 | **Capability** | An extension's effective permission is `manifest ∩ policy ∩ user grant`. Never granted without consent UI. |
| 6 | **No Hidden Authority** | Nothing causes a state change the Core cannot see and audit. Only the Core emits domain events. |
| 7 | **No Laundering** | No automated process can lower a trust level. Summarizing, extracting, or memorizing does not remove taint. |
| 8 | **Unautomatable Consent** | Lumi cannot operate its own permission dialog. The Shell refuses unconditionally. |

Full text and rationale: [docs/contracts/invariants.md](docs/contracts/invariants.md).

### Tech stack

| Area | Choice |
|---|---|
| Desktop Shell | Tauri 2 (abstracted behind `PlatformShell`, keeping an Electron escape hatch) |
| AI Core | Python / asyncio, single process, **the hub** |
| Audio I/O | Inside Core (keeps the barge-in critical path in one process) |
| Memory | SQLite + sqlite-vec + FTS5, whole-database encryption (see below); embeddings via Harrier-OSS-v1 270M (ONNX q4 / 640-dim / CPU) |
| LLM | Ollama (Qwen3 / Gemma3 family) |
| STT / VAD | faster-whisper (CTranslate2, int8) / Silero VAD (ONNX, CPU) |
| TTS | AivisSpeech / VOICEVOX (separate process; GPU when CUDA is available, otherwise CPU) |
| Character | VRM via [`@pixiv/three-vrm`](https://github.com/pixiv/three-vrm) — Live2D planned for Phase 9 |
| License | **Core is MIT.** No GPL/AGPL or non-OSS code enters the Core. |

**The Core does not depend on torch** — installer size is a tracked constraint.

### What "encrypted" means, precisely

Every database holding conversation-derived data — memory, events, audit log — is encrypted
**page-level, whole-file**, using ChaCha20 via
[SQLite3 Multiple Ciphers](https://github.com/utelle/SQLite3MultipleCiphers) (through APSW).
This is not application-level field encryption, and it is not "we rely on BitLocker."

The key is 256 bits, generated once per user, and stored in the OS secret store — DPAPI at
current-user scope on Windows. **You never create or manage a password.** There is no plaintext
fallback: on a platform with no secret-store implementation, opening the database fails and Lumi
stops rather than silently writing your conversations out in the clear.

What this protects and what it does not:

| Protects against | Does **not** protect against |
|---|---|
| Someone reading the disk directly | Malware running as your own user account |
| The database files being copied off the machine | A process with OS administrator privileges |
| The files ending up in a backup or cloud sync | Files you export yourself (exports are plaintext, and say so) |
| A repaired or resold PC's disk being read | |

DPAPI is current-user scoped, so **any process running as you can open the key the same way Lumi
does.** That is the definition of the mechanism, not a gap in it. Details:
[docs/contracts/privacy.md](docs/contracts/privacy.md) §3.

---

## Current progress & roadmap

Every phase must be **a usable product on its own**. If development stopped at Phase 1, what you
have is still "a desktop character that talks."

|  | Phase | What it establishes | Status |
|---|---|---|---|
| **0** | Walking Skeleton | Transparent click-through window, Python sidecar packaging, first-run setup, credits screen — every dangerous integration point, with zero intelligence | ✅ **Done** (2026-08-16) |
| **1** | MVP — Talking Desktop Character | Mic → VAD → STT → LLM → TTS → lip-sync, real barge-in, and the Kernel foundation (Attention Arbiter, cancellation contract, provenance, event bus) | ✅ **Done** (2026-08-22) |
| **2** | Memory | Encrypted storage, speculative STT, episodes + retention, MemoryStore, hybrid retrieval, reflection, memory UI | 🟡 **In progress** — implementation complete, field validation remains. **Not in a release yet** |
| **3** | World Model + Internal State + autonomous speech | Sensors, drives, mood / fatigue, autonomy gate and budget. **Speech only — no OS operations yet** | ⬜ Next |
| **4a** | Kernel + `fs` | Real Tool Registry, canonicalizer, bind verifier, permission prompt UI, grants, hash-chained audit log | ⬜ Planned |
| **4b** | `browser` | Class B tools, result verification, out-of-process browser extension | ⬜ Planned |
| **4c** | `computer` | Screenshot + input injection. Blocked until the Invariant 8 gaps are settled | ⬜ Planned |
| **5** | Vision + Model Resource Manager | VRAM admission control, LRU eviction, on-demand VLM loading | ⬜ Planned |
| **6** | Autonomous Life | Phase 3 × Phase 4 — autonomy that is allowed to use tools | ⬜ Planned |
| **7** | Widget / Gamelet | Sandboxed widget broker, AI-generated games | ⬜ Planned |
| **8** | Game Agent | Three-layer control (strategy / tactics / reflex), game adapters | ⬜ Planned |
| **9** | Third-party extensions / Live2D | Extension SDK, manifest signing, Live2D renderer | ⬜ Planned |

**Completion is judged by living with it, not by a benchmark.** Phase 3 is done when a full day
with Lumi running is not unpleasant. Phase 2 is done when it correctly recalls old conversations,
lets stale topics fade, and can say "but you said the opposite before."

### Measured so far

|  |  |
|---|---|
| Voice turn latency | **p50 1.50 s / warm p95 1.63 s** — meets the p95 < 2.0 s SLO. *On a GPU configuration*; on CPU, TTS alone costs ~0.9 s and the budget does not close. |
| Installer size | **87 MB** (v0.1.1). Roughly half of that is the STT/VAD inference stack — CTranslate2 and ONNX Runtime — plus a 24 MB bundled VRM character. Avoiding torch is what keeps this two orders of magnitude below the 1–2 GB it would otherwise be. |
| Idle VRAM | **55 MiB** |

Details — and, just as importantly, **what each measurement does not guarantee** — are in
[docs/measurements/](docs/measurements/). The latency numbers in particular were taken by
injecting recorded audio offline, not by talking to it, and not on the machine used to verify
that setup works.

### What is deliberately *not* being built

Cloud services, multi-user, accounts, billing · web and mobile versions · fully unattended
autonomy · a general-purpose agent framework · training and fine-tuning infrastructure ·
**impersonating real people** (a structural constraint, not a missing feature).

---

## Influences and prior art

Lumi did not appear from nowhere. Three things shaped it, in different ways.

### Neuro-sama — the reason this is worth building

[Neuro-sama](https://www.twitch.tv/vedal987) (by Vedal) is the clearest demonstration that an AI
character can read as *someone* rather than *something*: it interrupts, it remembers running jokes,
it has opinions it keeps, and it reacts in real time rather than in request/response turns. That
is the experience Lumi is chasing — and it is why **barge-in and memory are the first two pillars
here, not features scheduled for later.** A character you have to wait politely for is a chat box
with a face.

Neuro-sama is closed-source and proprietary. There is no code relationship — the influence is
entirely on *what the target experience is*.

### Project AIRI — the reference implementation we studied

[Project AIRI](https://github.com/moeru-ai/airi) (MIT) is the most serious open attempt at this
shape of thing, and Lumi's design work started by reading it closely (v0.11.3, HEAD `c71de3a`).

**We borrow its ideas, not its code.** AIRI is MIT-licensed, so porting would be legal; we don't,
because its structure does not fit Lumi's requirements. To be precise about the relationship: this
is **not** a clean-room implementation — that term means a deliberate separation between the
people who read the source and the people who write the new one, and no such wall exists here.

What Lumi takes from AIRI: the tool-descriptor registry with fail-closed metadata; the
declaration-as-ceiling model for extension permissions; hard-won practical knowledge about
transparent always-on-top windows; sentence-level TTS segmentation with look-ahead generation and
ordered playback; inline markers in the LLM stream driving expression and motion; and the
three-layer perception/reflex/conscious split in its Minecraft integration.

Where Lumi deliberately diverges — and these are the reasons it is a separate project rather than
a fork:

| AIRI | Lumi |
|---|---|
| Voice input is suppressed while speaking | **Real barge-in** |
| Tool calls execute ungated, straight from IPC handlers | Every side effect passes the **Permission Kernel** (Invariant 2) |
| Extensions are `import()`ed into the privileged Electron main process | Third-party code always runs **out-of-process** |
| Manifest permissions are auto-granted with no consent step | Consent UI is mandatory; effective permission is an **intersection** (Invariant 5) |
| Long-term memory is a schema with no implementation behind it | Memory is the core of Phase 2 |
| ~60 WebSocket events choreographing module lifecycle | Explicit command sequences |

Full analysis, including what was found unimplemented and why each borrow/divergence was chosen:
[DESIGN.md](docs/DESIGN.md) §10.

### Ukagaka — the format

The [Ukagaka](https://en.wikipedia.org/wiki/Ukagaka) tradition (Materia / SSP, from around 2000)
established the thing Lumi is a descendant of: a character that occupies your desktop rather than
an application window, that you live alongside rather than launch. Lumi keeps the format and
replaces the scripted responses with an actual AI stack.

---

## Install

Download the installer from the
**[latest release](https://github.com/taka2360/Lumi/releases/latest)**
(`Lumi_x.y.z_x64-setup.exe`). Windows x64 only.

Bear in mind what the released build is: **Phase 1**. It listens, thinks, speaks, and can be
interrupted mid-sentence — but it does not remember anything once you close it. Memory is
implemented on `main` and is not in a release yet.

### First run

Lumi needs three things before it can hold a conversation: a **TTS engine**, an **LLM runtime**,
and an **STT model**. Setup walks you through each one, and every one is an explicit choice —
nothing is fetched until you say so.

**If you decline, Lumi does not start half-working.** It tells you what is still missing, how to
resolve it, and exits — and picks up where you left off next time you launch it. There is no
degraded mode where the character stands there and silently fails to hear you:

> A Lumi with no STT is *a picture that ignores you*. A Lumi with no LLM is *an object that
> transcribes and never answers*. Both read as "broken," not as "not set up yet."
> — [ADR-034](docs/decisions/ADR-034-gate-startup-on-complete-setup.md)

Once all three are ready, the character appears and the microphone opens.

---

## Running from source

**Prerequisites** — Rust (MSVC toolchain) · Node 24+ · pnpm 11 · [uv](https://docs.astral.sh/uv/)
(uv fetches Python 3.12 itself). Windows only for now.

```bash
git clone https://github.com/taka2360/Lumi.git
cd Lumi
pnpm install
cd core && uv sync && cd ..

pnpm dev            # launch the app (Shell + Stage, with Core as a sidecar)
```

The first run goes through the same setup described above.

### Common commands

| What | Command | Where |
|---|---|---|
| Launch the app | `pnpm dev` | repo root |
| Build the installer | `pnpm build` | repo root |
| Stage only | `pnpm stage:dev` | repo root |
| Core: set up / run / test | `uv sync` · `uv run lumi-core` · `uv run pytest` | `core/` |
| Core: lint / format / types | `uv run ruff check` · `uv run ruff format` · `uv run mypy` | `core/` |
| Stage: test / lint / types | `pnpm test` · `pnpm lint` · `pnpm typecheck` | `stage/` |
| Shell: test / lint / format | `cargo test` · `cargo clippy --all-targets -- -D warnings` · `cargo fmt` | `shell/src-tauri/` |

---

## Repository layout

```
Lumi/
├── docs/          Design — the single source of truth. Changes here precede code
├── core/          Lumi Core — Python / asyncio. Authority: decisions, state, policy, memory
├── shell/         Lumi Shell — Tauri 2 / Rust. OS privileged primitives only
├── stage/         Stage WebView — React + TS + Zustand. Presentation only
├── extensions/    [Phase 5+] Out-of-process capability extensions
└── content/       Content Pack — character, model, voice, persona. Contains no code
```

## Documentation

The design documents are the single source of truth, and **they are written in Japanese**.
Design changes land before the code that implements them.

| Start here |  |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | The design constitution — vision, non-goals, principles, architecture |
| [docs/roadmap.md](docs/roadmap.md) | What gets built when, and what must be decided before each phase starts |
| [docs/contracts/](docs/contracts/) | Invariants, security boundaries, provenance, privacy, event model — all Confirmed |
| [docs/architecture/](docs/architecture/) | Per-area design: core, agent, memory, audio, autonomy, permission, UI |
| [docs/decisions/](docs/decisions/) | ADRs — every significant decision, recorded at the time it was made |

---

## License and third-party components

Lumi's own code — Core, Shell, and Stage — is **[MIT licensed](LICENSE)**.

**Distributables contain only components whose redistribution is explicitly permitted.**
Everything else is fetched at first run, from its official source, based on an explicit choice:

| Component | Bundled | How you get it |
|---|---|---|
| Lumi Core / Shell / Stage | ✓ | MIT, ours |
| Silero VAD (ONNX) | ✓ | Bundled — it sits on the barge-in critical path, so it is never fetched at runtime |
| AivisSpeech Engine | ✗ | Fetched at first run from the official source, on your explicit choice |
| VOICEVOX Engine | ✗ | Installed separately by you — bundling is prohibited by its terms |
| Ollama and LLM models | ✗ | Ollama is detected, never fetched. Models are pulled through Ollama only after explicit consent |
| STT / embedding models | ✗ | Fetched at first run, on your explicit choice (pinned URL + SHA-256 verified) |
| VRM character model | Depends | Ships in the Content Pack when the model's terms permit redistribution |

The full analysis — including credit obligations and the parts still marked *unverified* — is in
[docs/licensing.md](docs/licensing.md). Unverified components are never shipped: fail-closed.

Third-party OSS notices are generated from the actual dependency graphs of all three subprojects,
and **the build fails if a GPL/AGPL dependency appears**.

> This is not legal advice. It is a developer's reading of the terms, recorded with its date.

---

## Contributing and security

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — the design-before-code workflow, what the invariants
  mean for a pull request, and how to get a change reviewed.
- **[SECURITY.md](SECURITY.md)** — the threat model, what Lumi does and does **not** protect
  against, and how to report a vulnerability privately.

Lumi is an unusually opinionated codebase: the constraints are written down, and code that
violates them is a defect regardless of how well it works. Reading
[docs/DESIGN.md](docs/DESIGN.md) before opening a pull request will save you time.
