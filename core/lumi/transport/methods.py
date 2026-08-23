"""The names that go on the wire. **`docs/contracts/wire.json` is authoritative** (→ ADR-022).

This module is the Python-side mirror of that contract, and nothing more. It holds
**values only** and imports nothing under `lumi`, so any module that has to name a
method can reach it without acquiring a dependency in the process.

## Why they live in one place

They used to be declared next to whoever sent them — six modules between them. Nothing
was wrong with any single one; the problem was that **there was no place to look** to
answer "what can Core send?", and `test_wire_contract.py` had to import from six
modules (one of them from inside a test function) to ask the question at all.

`STAGE_METHODS` and `INBOUND_METHODS` exist so that question has a single answer, and
so that adding a constant without adding it to the contract **fails a test** rather
than quietly becoming a method nobody documented.

## What this does not do

**Nothing here is read at runtime to decide anything.** The allowlist for the inbound
direction is `WsServer.on_request`'s registry, not `INBOUND_METHODS` (ADR-028) — a
route exists because somebody registered a handler for it, never because a name appears
in a tuple. This module pins the contract alongside that registry; it does not become it.
"""

from __future__ import annotations

from typing import Final

# ── Core → Stage ──────────────────────────────────────────────

#: Setup state for all three components, plus the derived boot phase. A notify.
METHOD_SETUP_STATE: Final = "stage.setup.state"

#: Asks whether to fetch a component. **A command** — Core waits for the answer.
METHOD_SETUP_PROMPT: Final = "stage.setup.prompt"

#: Speech start and end. Contract → docs/interfaces/renderer.md
METHOD_SPEECH_STARTED: Final = "stage.speech.started"
METHOD_SPEECH_ENDED: Final = "stage.speech.ended"

#: What Core heard the user say. **Sent before the Activity is proposed**, so a turn
#: that goes nowhere still leaves the heard text on screen.
METHOD_USER_SAID: Final = "stage.user.said"

#: Which model to draw, and the credit that goes with it (ADR-029). Sent once at startup.
METHOD_MODEL: Final = "stage.character.model"

#: The expression changed. **A notify, not a command** — Core never waits to be told a
#: face finished changing.
METHOD_EXPRESSION: Final = "stage.character.expression"

#: The effective settings and **where each value came from**.
#:
#: **Still sent to the Stage even though settings moved to their own window** (ADR-042):
#: the character window reads `locale` from it, and a locale change has to reach the
#: bubble as surely as it reaches the settings table.
METHOD_SETTINGS: Final = "stage.settings.state"

#: Whether the microphone is open, and whether the user muted it.
#:
#: **On the character window on purpose** (ui.md §5b). "Is it listening?" must not live
#: inside a window that can be closed — closed is the state it would spend its life in.
METHOD_MIC: Final = "stage.audio.mic"

#: Everything Core may send to the Stage. **Cross-checked against the contract**
#: (`test_wire_contract.py`), which is what makes forgetting to declare one a failure.
STAGE_METHODS: Final[frozenset[str]] = frozenset(
    {
        METHOD_SETUP_STATE,
        METHOD_SETUP_PROMPT,
        METHOD_SPEECH_STARTED,
        METHOD_SPEECH_ENDED,
        METHOD_USER_SAID,
        METHOD_MODEL,
        METHOD_EXPRESSION,
        METHOD_SETTINGS,
        METHOD_MIC,
    }
)

# ── Core → Panel (ADR-042) ────────────────────────────────────

#: The same settings snapshot the Stage gets, for the settings window.
METHOD_PANEL_SETTINGS: Final = "panel.settings.state"

#: The Activity tree and the latest turn's latency breakdown.
#:
#: **This used to be `stage.inspector.state`.** It is not sent to the character window at
#: all any more, so the per-turn inspector payload no longer shares a connection with
#: speech and barge-in (ADR-042).
METHOD_PANEL_INSPECTOR: Final = "panel.inspector.state"

#: Memory changed underneath an open memory window — a reflection pass wrote something.
#: **A nudge, not the data**: the window asks for what it wants to show.
METHOD_PANEL_MEMORY: Final = "panel.memory.state"

#: Everything Core may send to a panel window. Cross-checked against the contract.
PANEL_METHODS: Final[frozenset[str]] = frozenset(
    {
        METHOD_PANEL_SETTINGS,
        METHOD_PANEL_INSPECTOR,
        METHOD_PANEL_MEMORY,
    }
)

# ── Stage → Core ──────────────────────────────────────────────

#: The Stage asks; Core validates, decides, writes, and broadcasts the result (ADR-028).
METHOD_SETTINGS_UPDATE: Final = "stage.settings.update"

#: Re-runs Ollama's local-only detection from the setup screen. The automatic timer and
#: the visible button use the same route, so there is only one decision path.
METHOD_SETUP_RECHECK_OLLAMA: Final = "stage.setup.recheck_ollama"

#: Mutes or unmutes the microphone. **From the character window**, next to the light
#: that says the microphone is open.
METHOD_MIC_MUTE: Final = "stage.audio.mute"

# ── Panel → Core (ADR-042) ────────────────────────────────────

#: The settings window asks for the same change the Stage could ask for. **Two names for
#: one handler**: the namespace is what tells Core which window asked, and a role may
#: only send its own.
METHOD_PANEL_SETTINGS_UPDATE: Final = "panel.settings.update"

#: Reading what is remembered. Text search is optional; without it, the most recent.
METHOD_PANEL_MEMORY_SEARCH: Final = "panel.memory.search"

#: Correcting a memory. **Supersedes rather than overwrites** — the correction is a new
#: record, and what it corrected stays readable (memory.md §8).
METHOD_PANEL_MEMORY_EDIT: Final = "panel.memory.edit"

#: Deleting a memory. **Physically**, through the one file that may delete user data
#: (privacy.md §5).
METHOD_PANEL_MEMORY_FORGET: Final = "panel.memory.forget"

#: "This is right." **The only escalation to `user_confirmed` / TRUSTED** (Invariant 7).
METHOD_PANEL_MEMORY_CONFIRM: Final = "panel.memory.confirm"

#: Writing everything remembered to a file the user chose. **The output is plain text**,
#: and the window says so before it is written.
METHOD_PANEL_MEMORY_EXPORT: Final = "panel.memory.export"

#: What "erase everything" would delete, counted per row of privacy.md §2. **Asked before
#: erasing, never after** — a confirmation with no numbers in it is not informed consent.
METHOD_PANEL_MEMORY_ERASE_PREVIEW: Final = "panel.memory.erase_preview"

#: Erase everything.
METHOD_PANEL_MEMORY_ERASE: Final = "panel.memory.erase"

#: What a client may initiate. **A tuple, because the contract's order is checked too.**
#:
#: **Namespaces are not decoration here.** A method's prefix decides which role can send
#: it (`method_matches_role`), so `panel.memory.erase` is unreachable from the character
#: window — not by a check somewhere, but because the token it holds is a different one.
INBOUND_METHODS: Final[tuple[str, ...]] = (
    METHOD_SETTINGS_UPDATE,
    METHOD_SETUP_RECHECK_OLLAMA,
    METHOD_MIC_MUTE,
    METHOD_PANEL_SETTINGS_UPDATE,
    METHOD_PANEL_MEMORY_SEARCH,
    METHOD_PANEL_MEMORY_EDIT,
    METHOD_PANEL_MEMORY_FORGET,
    METHOD_PANEL_MEMORY_CONFIRM,
    METHOD_PANEL_MEMORY_EXPORT,
    METHOD_PANEL_MEMORY_ERASE_PREVIEW,
    METHOD_PANEL_MEMORY_ERASE,
)

# ── Payload values ────────────────────────────────────────────

#: The choices the Stage returns in `result`. `select` means an already-local model;
#: `skip` isn't used in any comparison, but both are declared so **only one side of the
#: contract isn't documented**.
#:
#: The Stage labels `CHOICE_INSTALL` "retry" after a failure — **same choice, and the
#: retry count is deliberately unbounded** since the press always comes from the user
#: (ADR-034).
CHOICE_INSTALL: Final = "install"
CHOICE_SKIP: Final = "skip"
CHOICE_SELECT: Final = "select"

#: The answer to the one question asked before the others: "fetch all of this?"
#:
#: **Its own value rather than reusing `select`.** For `llm_model`, `select` means "use a
#: model already on this machine"; here the user is choosing *how to be asked*, and giving
#: one word two unrelated meanings is how a contract stops being readable.
CHOICE_INDIVIDUALLY: Final = "individually"

#: Which component a question is about. **The panel has to say what it is fetching** —
#: "may I download this?" without a subject is not consent.
COMPONENT_TTS: Final = "tts"
COMPONENT_STT: Final = "stt"
COMPONENT_LLM_MODEL: Final = "llm_model"
#: The embedding model (ADR-041). **The only optional one** — declining it costs
#: similarity search, not the ability to hold a conversation.
COMPONENT_EMBEDDING: Final = "embedding"
#: Not a component: the question that offers **everything missing at once**, with the
#: total. The payload carries `items` and `total_bytes` alongside the usual fields.
COMPONENT_ALL: Final = "all"

#: Why there is no character model to draw (ADR-036). **A code, never a sentence** —
#: Core does not know the Stage's locale, and a display string sent from here is the one
#: line on screen that a language change cannot reach.
REASON_MODEL_NOT_IN_PACK: Final = "model_not_in_pack"
REASON_PACK_UNREADABLE: Final = "pack_unreadable"

#: Cross-checked against the contract, like the method names above.
CHARACTER_MODEL_REASONS: Final[tuple[str, ...]] = (
    REASON_MODEL_NOT_IN_PACK,
    REASON_PACK_UNREADABLE,
)
