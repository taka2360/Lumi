<p align="center">
  <img src="assets/branding/banner.png" alt="Lumi" width="100%">
</p>

<h1 align="center">Lumi</h1>

<p align="center">
  <strong>It listens. It remembers. It lives on your desktop. Not a chatbot in a window.</strong>
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
> **Lumi is early software under active development.** Windows only, with rough edges and
> breaking changes.
>
> The **[latest release](https://github.com/taka2360/Lumi/releases/latest)** is a Phase 1 build.
> → [Install](#install) / [Current progress](#current-progress--roadmap)

---

## What is Lumi?

A desktop companion in the lineage of **[Ukagaka](https://en.wikipedia.org/wiki/Ukagaka)** —
a character that lives on your desktop rather than inside an app window — rebuilt on a modern,
fully local AI stack (LLM / STT / TTS / Vision / vector memory).

Rather than "a chat UI with an avatar attached," the aim is something that *lives* in your PC: it
hears you, remembers you, knows what's going on right now, and occasionally has something of its
own to say.

<!-- TODO: a 5-15s screen capture belongs here — ideally showing barge-in: Lumi talking,
     the user cutting in, and playback stopping mid-word. Save it as
     assets/branding/demo.gif and reference it with:
     <p align="center"><img src="assets/branding/demo.gif" alt="Lumi in use" width="100%"></p> -->

### What sets Lumi apart

|  |  |  |
|---|---|---|
| 🗣️ | **Full barge-in** | You can cut Lumi off mid-sentence. Playback mutes inside the audio path, not after the next LLM token |
| 🧠 | **Memory** | It remembers, forgets, and can hold a contradiction. "You told me the opposite last week" |
| 🛡️ | **Safe autonomy** | It acts on its own, but every side effect passes the Permission Kernel, and **it can't click its own consent dialog** |

### What "local" means

|  | Definition | Lumi |
|---|---|---|
| Air-gapped | Never touches the network | ✗ |
| Local-first | Core is local, external is supplementary | — |
| **Network-optional** | **External communication is optional and explicit** | **✓** |

**Inference, state, and decisions are all local.** With the conversation features as they exist
today, once Lumi is set up a conversation involves no network at all: speech recognition, the
language model, and speech synthesis all run on your machine. Setup is the one point where
anything is fetched, and **no component is fetched without asking first.**

A cloud LLM, if one ever exists, wouldn't be "the core moving to the cloud" — it would be an LLM
provider handed the network capability, described and gated as exactly that. ("Network-optional"
is a defined term here, not a general claim — see [DESIGN.md](docs/DESIGN.md) §1.)

---

## How it is built

Three processes, with sharply separated authority.

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

### The eight invariants

These are constraints, not features — they aren't broken at any stage of implementation.

| # | Name | Rule |
|---|---|---|
| 1 | **Authority** | Only the Core Kernel decides permissions |
| 2 | **Tool Gate** | Every operation with a side effect goes through the Permission Kernel |
| 3 | **Untrusted Data** | External text, images, files, web content, and game screens are **data, not instructions** |
| 4 | **Attention** | Exactly one Activity is in the foreground, always |
| 5 | **Capability** | An extension's effective permission is `manifest ∩ policy ∩ user grant` |
| 6 | **No Hidden Authority** | Nothing causes a state change the Core can't see and audit |
| 7 | **No Laundering** | No automated process lowers a trust level. Summarizing, extracting, and memorizing all preserve taint |
| 8 | **Unautomatable Consent** | Lumi can't operate its own permission dialog |

Full text and rationale: [docs/contracts/invariants.md](docs/contracts/invariants.md).

### Tech stack

| Area | Choice |
|---|---|
| Desktop Shell | Tauri 2 (abstracted behind `PlatformShell`, keeping an Electron escape hatch) |
| AI Core | Python / asyncio, single process, **the hub** |
| Audio I/O | Inside Core (keeps the barge-in critical path in one process) |
| Memory | SQLite + sqlite-vec + FTS5; embeddings via Harrier-OSS-v1 270M (ONNX q4 / 640-dim / CPU) |
| LLM | Ollama (Qwen3 / Gemma3 family) |
| STT / VAD | faster-whisper (CTranslate2, int8) / Silero VAD (ONNX, CPU) |
| TTS | AivisSpeech / VOICEVOX (separate process; GPU when CUDA is available, otherwise CPU) |
| Character | VRM via [`@pixiv/three-vrm`](https://github.com/pixiv/three-vrm) — Live2D planned for Phase 9 |
| License | **Core is MIT.** No GPL/AGPL or non-OSS code enters the Core |

**The Core does not depend on torch** — installer size is a tracked constraint.

For databases holding conversation-derived data (memory, events, audit log), every page of the
database file is encrypted with ChaCha20. The 256-bit key lives in the OS secret store (DPAPI on
Windows), so you never create or manage a password, and **there is no plaintext fallback.** What
that protects against, and what it doesn't, is in
[docs/contracts/privacy.md](docs/contracts/privacy.md) §3.

That's Phase 2, on `main`. **The current release is Phase 1**, which keeps no conversation history
on disk at all and leaves its event and audit databases unencrypted.

---

## Current progress & roadmap

Every phase has to be **a usable product on its own**. If development stopped at Phase 1, what you
have is still "a desktop character that talks."

|  | Phase | What it establishes | Status |
|---|---|---|---|
| **0** | Walking Skeleton | Transparent click-through window, sidecar packaging, first-run setup — every dangerous integration point, with zero intelligence | ✅ Done (2026-08-16) |
| **1** | MVP — Talking Desktop Character | Mic → VAD → STT → LLM → TTS → lip-sync, real barge-in, and the Kernel foundation | ✅ Done (2026-08-22) |
| **2** | Memory | Encrypted storage, speculative STT, episodes + retention, hybrid retrieval, reflection, memory UI | 🟡 **In progress** — implementation complete, field validation remains. **Not released yet** |
| **3** | World Model + Internal State + autonomous speech | Sensors, drives, autonomy gate and budget. **Speech only — no OS operations yet** | ⬜ Next |
| **4a** | Kernel + `fs` | Tool Registry, canonicalizer, bind verifier, permission prompt UI, audit log | ⬜ Planned |
| **4b** | `browser` | Class B tools, result verification, a minimal extension foundation, out-of-process browser extension | ⬜ Planned |
| **4c** | `computer` | Screenshot + input injection, once the Invariant 8 gaps are settled | ⬜ Planned |
| **5** | Vision + Model Resource Manager | VRAM admission control, LRU eviction, on-demand VLM loading | ⬜ Planned |
| **6** | Autonomous Life | Phase 3 × Phase 4 — autonomy that can use tools | ⬜ Planned |
| **7** | Widget / Gamelet | Sandboxed widget broker, AI-generated games | ⬜ Planned |
| **8** | Game Agent | Three-layer control (strategy / tactics / reflex), game adapters | ⬜ Planned |
| **9** | Third-party extensions / Live2D | Extension SDK, manifest signing, Live2D renderer | ⬜ Planned |

Completion is judged by living with it rather than by a benchmark — Phase 3 is done when a full
day with Lumi running isn't unpleasant.

### Measured so far

| | |
|---|---|
| Voice turn latency | **p50 1.50 s / warm p95 1.63 s** (SLO: p95 < 2.0 s). *On a GPU configuration* — on CPU, TTS alone costs ~0.9 s and the budget doesn't close |
| Installer size | **87 MB** (v0.1.1). About half is the STT/VAD inference stack, plus a 24 MB bundled VRM character |
| Idle VRAM | **55 MiB** |

The latency numbers came from injecting recorded audio offline, not from talking to it.
The details, and what each measurement doesn't guarantee, are in the docs.
→ [docs/measurements/](docs/measurements/)

### What is deliberately *not* being built

Cloud services, multi-user, accounts, billing · web and mobile versions · fully unattended
autonomy · a general-purpose agent framework · training and fine-tuning infrastructure ·
**impersonating real people** (a structural constraint, not a missing feature).

### Influences

- **[Neuro-sama](https://www.twitch.tv/vedal987)** — the target experience. Barge-in and memory
  being the first two pillars rather than later features comes from here
- **[Project AIRI](https://github.com/moeru-ai/airi)** — studied closely as a reference
  implementation. Design ideas borrowed, no code ported (→ [DESIGN.md](docs/DESIGN.md) §10)
- **[Ukagaka](https://en.wikipedia.org/wiki/Ukagaka)** — the format itself: a character that
  occupies your desktop rather than an application window

---

## Install

Grab the installer from the
**[latest release](https://github.com/taka2360/Lumi/releases/latest)**
(`Lumi_x.y.z_x64-setup.exe`). Windows x64 only.

The released build is **Phase 1**: it listens, thinks, speaks, and can be interrupted mid-sentence,
but **it doesn't remember anything once you close it.** Memory is on `main` and isn't in a release
yet.

### First run

Lumi needs three things to hold a conversation: a **TTS engine**, an **LLM runtime**, and an
**STT model**. Setup walks you through each, and every one is an explicit choice — nothing is
fetched until you say so.

**If you decline, Lumi doesn't start half-working.** It tells you what's missing and how to
resolve it, then exits and picks up where you left off next time. This avoids the case where the
character is standing there while silently failing to hear you (→
[ADR-034](docs/decisions/ADR-034-gate-startup-on-complete-setup.md)).

---

## Running from source

**Prerequisites** — Rust (MSVC toolchain) · Node 24+ · pnpm 11 · [uv](https://docs.astral.sh/uv/)
(uv fetches Python 3.12 itself). Windows only.

```bash
git clone https://github.com/taka2360/Lumi.git
cd Lumi
pnpm install
cd core && uv sync && cd ..

pnpm dev            # launch the app (Shell + Stage, with Core as a sidecar)
```

| What | Command | Where |
|---|---|---|
| Launch / build the installer | `pnpm dev` · `pnpm build` | repo root |
| Core: set up / run / test | `uv sync` · `uv run lumi-core` · `uv run pytest` | `core/` |
| Core: lint / format / types | `uv run ruff check` · `uv run ruff format` · `uv run mypy` | `core/` |
| Stage: test / lint / types | `pnpm test` · `pnpm lint` · `pnpm typecheck` | `stage/` |
| Shell: test / lint / format | `cargo test` · `cargo clippy --all-targets -- -D warnings` · `cargo fmt` | `shell/src-tauri/` |

---

## Repository layout and docs

```
Lumi/
├── docs/          Design — the single source of truth. Changes here precede code
├── core/          Lumi Core — Python / asyncio. Authority: decisions, state, policy, memory
├── shell/         Lumi Shell — Tauri 2 / Rust. OS privileged primitives only
├── stage/         Stage WebView — React + TS + Zustand. Presentation only
├── extensions/    [Phase 5+] Out-of-process capability extensions
└── content/       Content Pack — character, model, voice, persona. Contains no code
```

The design documents are the single source of truth, and **they're written in Japanese.**
Design changes land before the code that implements them.

| Start here | |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | The design constitution — vision, non-goals, principles, architecture |
| [docs/roadmap.md](docs/roadmap.md) | What gets built when, and what must be decided before each phase |
| [docs/contracts/](docs/contracts/) | Invariants, security boundaries, provenance, privacy — all Confirmed |
| [docs/architecture/](docs/architecture/) | Per-area design: core, agent, memory, audio, autonomy, permission, UI |
| [docs/decisions/](docs/decisions/) | ADRs — decisions recorded at the time they were made |

---

## License and third-party components

Lumi's own code (Core, Shell, Stage) is **[MIT licensed](LICENSE)**.

**Distributables contain only components whose redistribution is explicitly permitted.**
Everything else is fetched at first run, from its official source, based on an explicit choice:

| Component | Bundled | How you get it |
|---|---|---|
| Lumi Core / Shell / Stage | ✓ | MIT, ours |
| Silero VAD (ONNX) | ✓ | Bundled — it sits on the barge-in critical path, so it's never fetched at runtime |
| AivisSpeech Engine | ✗ | Fetched at first run from the official source, on your explicit choice |
| VOICEVOX Engine | ✗ | Installed separately by you — bundling is prohibited by its terms |
| Ollama and LLM models | ✗ | Ollama is detected, never fetched. Models are pulled through Ollama after explicit consent |
| STT / embedding models | ✗ | Fetched at first run (pinned URL + SHA-256 verified) |
| VRM character model | Depends | Ships in the Content Pack when the model's terms permit redistribution |

The full analysis, including credit obligations and the parts still marked *unverified*, is in
[docs/licensing.md](docs/licensing.md) — unverified components are never shipped. OSS notices are
generated from the actual dependency graphs, and the build fails if a GPL/AGPL dependency appears.

> This isn't legal advice — it's a developer's reading of the terms, recorded with its date.

---

## Contributing and security

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — the design-before-code workflow, and what the
  invariants mean for a pull request
- **[SECURITY.md](SECURITY.md)** — the threat model, what Lumi does and does **not** protect
  against, and how to report a vulnerability privately

The constraints here are written down, and code that violates them is treated as a defect even
when it works well. Reading [docs/DESIGN.md](docs/DESIGN.md) before opening a pull request will
save you time.
