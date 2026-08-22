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

#: The Activity tree and the latest turn's latency breakdown.
METHOD_INSPECTOR: Final = "stage.inspector.state"

#: The effective settings and **where each value came from**.
METHOD_SETTINGS: Final = "stage.settings.state"

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
        METHOD_INSPECTOR,
        METHOD_SETTINGS,
    }
)

# ── Stage → Core ──────────────────────────────────────────────

#: The Stage asks; Core validates, decides, writes, and broadcasts the result (ADR-028).
METHOD_SETTINGS_UPDATE: Final = "stage.settings.update"

#: Re-runs Ollama's local-only detection from the setup screen. The automatic timer and
#: the visible button use the same route, so there is only one decision path.
METHOD_SETUP_RECHECK_OLLAMA: Final = "stage.setup.recheck_ollama"

#: What the Stage may initiate. **A tuple, because the contract's order is checked too.**
INBOUND_METHODS: Final[tuple[str, ...]] = (
    METHOD_SETTINGS_UPDATE,
    METHOD_SETUP_RECHECK_OLLAMA,
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

#: Which component a question is about. **The panel has to say what it is fetching** —
#: "may I download this?" without a subject is not consent.
COMPONENT_TTS: Final = "tts"
COMPONENT_STT: Final = "stt"
COMPONENT_LLM_MODEL: Final = "llm_model"

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
