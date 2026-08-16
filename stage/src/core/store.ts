/**
 * The Stage's store. **Holds only what Core has broadcast via `stage.*`.**
 *
 * > The criterion: every value readable from the Stage's store should be something
 * > Core broadcast. If the Stage is computing its own state, logic has leaked in.
 * > → docs/architecture/ui.md §2
 *
 * The exceptions are `connected` (whether the WS is connected) and `prompt`
 * (whether Core is currently asking something). Both are **the state of the
 * connection itself**, not a judgment Lumi makes.
 */

import { create } from "zustand";

import type { VisemeTimeline } from "../character/lipsync";

/** The state of the engine **process**. A separate axis from installation state (`TtsSetupState`). */
export type EngineRuntime = "stopped" | "starting" | "ready" | "failed";

/**
 * The boot phase. **Core decides whether the character may be shown.**
 *
 * Defined in → docs/architecture/ui.md "Boot phases".
 * The Stage only switches screens based on this — **it never judges on its own**.
 */
export type BootPhase = "setup" | "installing" | "starting" | "ready";

export type TtsSetupState =
  | "unknown"
  | "not_configured"
  | "detected"
  | "installing"
  | "installed"
  | "failed";

/** Same shape as Core's `TtsSetup.to_payload()` (core/lumi/setup/state.py). */
export interface TtsSetupSnapshot {
  boot: BootPhase;
  state: TtsSetupState;
  engine_name: string | null;
  version: string | null;
  port: number | null;
  executable: string | null;
  reason: string | null;
  progress: number | null;
  runtime: EngineRuntime;
}

/** That speech is in progress, and its mouth timeline. **Time advances on the Stage's own clock.** */
export interface Speech {
  text: string;
  timeline: VisemeTimeline;
  /** The time it was received (`performance.now()`). */
  startedAtMs: number;
}

export interface SetupPrompt {
  /** Whether this is a re-prompt after a failure. */
  retry: boolean;
  /** The previous failure reason (only populated when `retry`). */
  reason: string | null;
}

interface StageState {
  connected: boolean;
  tts: TtsSetupSnapshot;
  prompt: SetupPrompt | null;
  speech: Speech | null;
  setConnected(connected: boolean): void;
  setTts(snapshot: TtsSetupSnapshot): void;
  setPrompt(prompt: SetupPrompt | null): void;
  setSpeech(speech: Speech | null): void;
}

const UNKNOWN_TTS: TtsSetupSnapshot = {
  // **Shows nothing before connecting.** The character never appears until Core says `ready`.
  boot: "starting",
  state: "unknown",
  engine_name: null,
  version: null,
  port: null,
  executable: null,
  reason: null,
  progress: null,
  runtime: "stopped",
};

export const useStageStore = create<StageState>((set) => ({
  connected: false,
  tts: UNKNOWN_TTS,
  prompt: null,
  speech: null,
  setConnected: (connected) => set({ connected }),
  setTts: (tts) => set({ tts }),
  setPrompt: (prompt) => set({ prompt }),
  setSpeech: (speech) => set({ speech }),
}));

/**
 * Values that go on the wire. **`docs/contracts/wire.json` is authoritative** (→ ADR-022).
 *
 * Types (`TtsSetupState`, etc.) vanish at runtime, so **checking a received value
 * against them needs an actual array**. The order matches Core's enum declaration
 * order (the order states progress).
 */
export const TTS_SETUP_STATES: readonly TtsSetupState[] = [
  "unknown",
  "not_configured",
  "detected",
  "installing",
  "installed",
  "failed",
];
export const ENGINE_RUNTIMES: readonly EngineRuntime[] = ["stopped", "starting", "ready", "failed"];
export const BOOT_PHASES: readonly BootPhase[] = ["setup", "installing", "starting", "ready"];

/** Coerces a payload from Core into the type. **Unknown values are treated as unknown** (never guessed at). */
export function toTtsSnapshot(payload: Record<string, unknown>): TtsSetupSnapshot {
  const state = payload.state;
  return {
    // **Never rounds an unknown phase to `ready`.** Rounding it would show the
    // character before it's ready (fail-closed to `starting` = loading instead).
    boot: BOOT_PHASES.find((candidate) => candidate === payload.boot) ?? "starting",
    state: TTS_SETUP_STATES.find((candidate) => candidate === state) ?? "unknown",
    runtime: ENGINE_RUNTIMES.find((candidate) => candidate === payload.runtime) ?? "stopped",
    engine_name: typeof payload.engine_name === "string" ? payload.engine_name : null,
    version: typeof payload.version === "string" ? payload.version : null,
    port: typeof payload.port === "number" ? payload.port : null,
    executable: typeof payload.executable === "string" ? payload.executable : null,
    reason: typeof payload.reason === "string" ? payload.reason : null,
    progress: typeof payload.progress === "number" ? payload.progress : null,
  };
}
