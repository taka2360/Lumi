/**
 * The Stage's wire constants match `docs/contracts/wire.json`.
 *
 * Contract and rules → docs/contracts/wire.md / ADR-022
 *
 * **The Stage fails closed on unknown values.** `toTtsSnapshot` rounds to the fail-closed
 * default (`"starting"` / `"unknown"`), and `parseTimeline` closes the mouth. A protocol
 * version mismatch is different: it is rejected explicitly and closes the connection.
 * This is where the static drift is caught.
 *
 * The same cross-check is also done by Core (`core/tests/test_wire_contract.py`)
 * and Shell (`shell/src-tauri/src/wire_contract.rs`).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { EMOTIONS } from "../character/expression";
import { VISEMES } from "../character/lipsync";
import {
  CMD_DRAG_START,
  CMD_OPEN_CREDITS,
  CMD_OPEN_OLLAMA_SITE,
  CMD_QUIT,
  CMD_SCALE,
  CMD_SET_HIT_REGION,
  CMD_SET_LOCALE,
  EVENT_HOVER_STATE,
} from "../platform/tauri";
import { CMD_CORE_ENDPOINT, EVENT_CORE_ENDPOINT } from "./connection";
import {
  CHARACTER_MODEL_REASONS,
  CHOICE_INSTALL,
  CHOICE_SKIP,
  METHOD_EXPRESSION,
  METHOD_INSPECTOR,
  METHOD_MODEL,
  METHOD_SETTINGS,
  METHOD_SETTINGS_UPDATE,
  METHOD_SETUP_PROMPT,
  METHOD_SETUP_RECHECK_OLLAMA,
  METHOD_SETUP_STATE,
  METHOD_SPEECH_ENDED,
  METHOD_SPEECH_STARTED,
  METHOD_USER_SAID,
} from "./methods";
import {
  BOOT_PHASES,
  ENGINE_RUNTIMES,
  LLM_SETUP_STATES,
  SETTINGS_SOURCES,
  SETUP_COMPONENTS,
  STT_SETUP_STATES,
  TTS_SETUP_STATES,
} from "./payloads";
import { PROTOCOL_VERSION } from "./protocol";

interface Wire {
  protocol_version: number;
  methods: { stage: string[]; os: string[] };
  setup_prompt_choices: { install: string; skip: string };
  tauri_events: { hover_state: string; core_endpoint: string };
  tauri_commands: string[];
  enums: {
    tts_setup_state: string[];
    engine_runtime: string[];
    boot_phase: string[];
    llm_setup_state: string[];
    stt_setup_state: string[];
    settings_source: string[];
    emotion: string[];
    viseme: string[];
  };
  setup_components: string[];
  character_model_reasons: string[];
  inbound_methods: string[];
}

/** The repo's `docs/contracts/wire.json`. **Never read at runtime** (docs aren't bundled in the distributable). */
const wire: Wire = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../../docs/contracts/wire.json"), "utf-8"),
) as Wire;

describe("wire contract", () => {
  it("protocol version", () => {
    // **All 3 languages must check this.** If only the Stage drifts, a command from
    // Core gets dropped by `parseCoreMessage`, and Core waits 10 seconds and times
    // out. Nothing shows on screen.
    expect(PROTOCOL_VERSION).toBe(wire.protocol_version);
  });

  it("stage's method names", () => {
    expect(
      new Set([
        METHOD_SETUP_STATE,
        METHOD_SETUP_PROMPT,
        METHOD_SPEECH_STARTED,
        METHOD_SPEECH_ENDED,
        METHOD_USER_SAID,
        METHOD_EXPRESSION,
        METHOD_INSPECTOR,
        METHOD_SETTINGS,
        METHOD_MODEL,
      ]),
    ).toEqual(new Set(wire.methods.stage));
  });

  it("os.* method names never appear in the Stage", () => {
    // **`stage.*` must never request OS privileges** (docs/architecture/core.md §3).
    // Confirms from the value side that the contract's os.* never mixes into the Stage-side constants.
    const stageConstants = [
      METHOD_SETUP_STATE,
      METHOD_SETUP_PROMPT,
      METHOD_SPEECH_STARTED,
      METHOD_SPEECH_ENDED,
      METHOD_USER_SAID,
      METHOD_EXPRESSION,
      METHOD_INSPECTOR,
      METHOD_SETTINGS,
    ];
    for (const method of wire.methods.os) {
      expect(stageConstants).not.toContain(method);
    }
  });

  it("what the Stage may ask Core to do", () => {
    // ★ **The Stage → Core direction** (ADR-028). The real allowlist is Core's registry;
    // this pins the Stage's constant against the same contract, so a rename on one side
    // fails here instead of turning into a silent `unknown_method` at runtime.
    expect([METHOD_SETTINGS_UPDATE, METHOD_SETUP_RECHECK_OLLAMA]).toEqual(wire.inbound_methods);
  });

  it("what a fetch question can be about", () => {
    // The panel picks its wording from this value. **Drift would ask permission to
    // fetch the wrong thing.**
    expect(SETUP_COMPONENTS).toEqual(wire.setup_components);
  });

  it("why there is no model to draw", () => {
    // **Core sends a code and the Stage owns the wording** (ADR-036). The Stage looks up
    // `character.model.<reason>` by exactly these names, so a code added on one side only
    // renders as the raw code — visible, but not what anyone intended.
    expect([...CHARACTER_MODEL_REASONS]).toEqual(wire.character_model_reasons);
  });

  it("the fetch-or-not choices", () => {
    expect({ install: CHOICE_INSTALL, skip: CHOICE_SKIP }).toEqual(wire.setup_prompt_choices);
  });

  it("Tauri event names", () => {
    // Dots aren't allowed, so `shell:hover:state` instead of `shell.hover.state`.
    // The actual entities on the Shell side are hover.rs / core_endpoint.rs.
    expect(EVENT_HOVER_STATE).toBe(wire.tauri_events.hover_state);
    expect(EVENT_CORE_ENDPOINT).toBe(wire.tauri_events.core_endpoint);
  });

  it("Tauri command names", () => {
    // **A one-sided check.** The Shell side is a `#[tauri::command]` function name,
    // which can't be retrieved as data (docs/contracts/wire.md §4 "What this does not guarantee").
    expect(
      new Set([
        CMD_SET_HIT_REGION,
        CMD_CORE_ENDPOINT,
        CMD_DRAG_START,
        CMD_SCALE,
        CMD_SET_LOCALE,
        CMD_OPEN_CREDITS,
        CMD_OPEN_OLLAMA_SITE,
        CMD_QUIT,
      ]),
    ).toEqual(new Set(wire.tauri_commands));
  });

  it("enum values that go on the wire", () => {
    // Order matters too. Core's enum declaration order (the order states progress) carries meaning.
    expect(TTS_SETUP_STATES).toEqual(wire.enums.tts_setup_state);
    expect(ENGINE_RUNTIMES).toEqual(wire.enums.engine_runtime);
    expect(BOOT_PHASES).toEqual(wire.enums.boot_phase);
    expect(LLM_SETUP_STATES).toEqual(wire.enums.llm_setup_state);
    expect(STT_SETUP_STATES).toEqual(wire.enums.stt_setup_state);
    expect(SETTINGS_SOURCES).toEqual(wire.enums.settings_source);
    expect(EMOTIONS).toEqual(wire.enums.emotion);
    expect(VISEMES).toEqual(wire.enums.viseme);
  });
});
