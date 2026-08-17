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

import type { ExpressionState } from "../character/expression";
import { parseExpression } from "../character/expression";
import { parseTimeline, type VisemeTimeline } from "../character/lipsync";

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

/** Whether Ollama is there. **Lumi never fetches it** (ADR-023), so there is no `installing`. */
export type LlmSetupState = "unknown" | "not_configured" | "detected" | "model_missing";

/** Whether the speech model is there. Same shape as TTS **minus `detected`** — there is no
 * such thing as an STT model the user installed separately (docs/architecture/setup.md §2b). */
export type SttSetupState = "unknown" | "not_configured" | "installing" | "installed" | "failed";

/** Which component a question is about. **A question with no subject is not consent.** */
export type SetupComponent = "tts" | "stt";

/** Same shape as Core's `TtsSetup.to_payload()` (core/lumi/setup/state.py). */
export interface TtsSetupSnapshot {
  state: TtsSetupState;
  engine_name: string | null;
  version: string | null;
  port: number | null;
  executable: string | null;
  reason: string | null;
  progress: number | null;
  runtime: EngineRuntime;
}

/** Same shape as Core's `LlmSetup.to_payload()`. **No progress** — nothing is ever fetched. */
export interface LlmSetupSnapshot {
  state: LlmSetupState;
  model: string | null;
  reason: string | null;
  runtime: EngineRuntime;
}

/** Same shape as Core's `SttSetup.to_payload()`. **No runtime** — a file, not a process. */
export interface SttSetupSnapshot {
  state: SttSetupState;
  model: string | null;
  reason: string | null;
  progress: number | null;
}

/**
 * All three inference components, plus the phase derived from them.
 *
 * **The phase is Core's to decide** (docs/architecture/setup.md §2b): the Stage never
 * works out for itself whether "no LLM" ought to keep the character hidden.
 */
export interface SetupSnapshot {
  boot: BootPhase;
  tts: TtsSetupSnapshot;
  llm: LlmSetupSnapshot;
  stt: SttSetupSnapshot;
}

/** That speech is in progress, and its mouth timeline. **Time advances on the Stage's own clock.** */
export interface Speech {
  text: string;
  timeline: VisemeTimeline;
  /** The time it was received (`performance.now()`). */
  startedAtMs: number;
}

/**
 * A timeline that moves nothing. Used when Core sends no `spans`.
 *
 * `visemeAt` returns `null` at every point, so **the mouth simply stays closed** —
 * which is what "no timeline" should mean.
 */
const NO_TIMELINE: VisemeTimeline = { spans: [], totalMs: 0 };

export interface SetupPrompt {
  /** What is being asked about. **The panel's wording depends on it.** */
  component: SetupComponent;
  /** Whether this is a re-prompt after a failure. */
  retry: boolean;
  /** The previous failure reason (only populated when `retry`). */
  reason: string | null;
}

interface StageState {
  connected: boolean;
  setup: SetupSnapshot;
  prompt: SetupPrompt | null;
  speech: Speech | null;
  expression: ExpressionState | null;
  setConnected(connected: boolean): void;
  setSetup(snapshot: SetupSnapshot): void;
  setPrompt(prompt: SetupPrompt | null): void;
  setSpeech(speech: Speech | null): void;
  setExpression(expression: ExpressionState | null): void;
}

const UNKNOWN_TTS: TtsSetupSnapshot = {
  state: "unknown",
  engine_name: null,
  version: null,
  port: null,
  executable: null,
  reason: null,
  progress: null,
  runtime: "stopped",
};

const UNKNOWN_LLM: LlmSetupSnapshot = {
  state: "unknown",
  model: null,
  reason: null,
  runtime: "stopped",
};

const UNKNOWN_STT: SttSetupSnapshot = {
  state: "unknown",
  model: null,
  reason: null,
  progress: null,
};

export const UNKNOWN_SETUP: SetupSnapshot = {
  // **Shows nothing before connecting.** The character never appears until Core says `ready`.
  boot: "starting",
  tts: UNKNOWN_TTS,
  llm: UNKNOWN_LLM,
  stt: UNKNOWN_STT,
};

export const useStageStore = create<StageState>((set) => ({
  connected: false,
  setup: UNKNOWN_SETUP,
  prompt: null,
  speech: null,
  expression: null,
  setConnected: (connected) => set({ connected }),
  setSetup: (setup) => set({ setup }),
  setPrompt: (prompt) => set({ prompt }),
  setSpeech: (speech) => set({ speech }),
  setExpression: (expression) => set({ expression }),
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
export const LLM_SETUP_STATES: readonly LlmSetupState[] = [
  "unknown",
  "not_configured",
  "detected",
  "model_missing",
];
export const STT_SETUP_STATES: readonly SttSetupState[] = [
  "unknown",
  "not_configured",
  "installing",
  "installed",
  "failed",
];
export const ENGINE_RUNTIMES: readonly EngineRuntime[] = ["stopped", "starting", "ready", "failed"];
export const BOOT_PHASES: readonly BootPhase[] = ["setup", "installing", "starting", "ready"];
export const SETUP_COMPONENTS: readonly SetupComponent[] = ["tts", "stt"];

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function part(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = payload[key];
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * Reads a `stage.setup.state` payload. **Unknown values are treated as unknown**, never guessed at.
 *
 * The whole snapshot is read at once because **the boot phase is a function of all three**.
 * Reading them from separate messages could not guarantee ordering.
 */
export function toSetupSnapshot(payload: Record<string, unknown>): SetupSnapshot {
  return {
    // **Never rounds an unknown phase to `ready`.** Rounding it would show the
    // character before it's ready (fail-closed to `starting` = loading instead).
    boot: BOOT_PHASES.find((candidate) => candidate === payload.boot) ?? "starting",
    tts: toTtsSnapshot(part(payload, "tts")),
    llm: toLlmSnapshot(part(payload, "llm")),
    stt: toSttSnapshot(part(payload, "stt")),
  };
}

export function toTtsSnapshot(payload: Record<string, unknown>): TtsSetupSnapshot {
  return {
    state: TTS_SETUP_STATES.find((candidate) => candidate === payload.state) ?? "unknown",
    runtime: ENGINE_RUNTIMES.find((candidate) => candidate === payload.runtime) ?? "stopped",
    engine_name: asString(payload.engine_name),
    version: asString(payload.version),
    port: asNumber(payload.port),
    executable: asString(payload.executable),
    reason: asString(payload.reason),
    progress: asNumber(payload.progress),
  };
}

export function toLlmSnapshot(payload: Record<string, unknown>): LlmSetupSnapshot {
  return {
    state: LLM_SETUP_STATES.find((candidate) => candidate === payload.state) ?? "unknown",
    runtime: ENGINE_RUNTIMES.find((candidate) => candidate === payload.runtime) ?? "stopped",
    model: asString(payload.model),
    reason: asString(payload.reason),
  };
}

export function toSttSnapshot(payload: Record<string, unknown>): SttSetupSnapshot {
  return {
    state: STT_SETUP_STATES.find((candidate) => candidate === payload.state) ?? "unknown",
    model: asString(payload.model),
    reason: asString(payload.reason),
    progress: asNumber(payload.progress),
  };
}

/**
 * Reads a `stage.setup.prompt` payload. **An unknown component falls back to the engine.**
 *
 * Never left blank: a consent dialog that fails to say what it is asking about is worse
 * than one that names the more likely subject.
 */
export function toSetupPrompt(payload: Record<string, unknown>): SetupPrompt {
  return {
    component: SETUP_COMPONENTS.find((candidate) => candidate === payload.component) ?? "tts",
    retry: payload.retry === true,
    reason: asString(payload.reason),
  };
}

/**
 * Reads a `stage.speech.started` payload.
 *
 * **A missing timeline never suppresses the text.** Core omits `spans` whenever the
 * engine returns no timing (docs/interfaces/renderer.md: better a still mouth than a
 * mouth on bogus timing) — but **not moving the mouth and not saying what was said are
 * different failures.** Dropping the whole event left the bubble blank for exactly the
 * utterances that most needed reading.
 */
export function toSpeech(payload: Record<string, unknown>, startedAtMs: number): Speech {
  return {
    text: typeof payload.text === "string" ? payload.text : "",
    timeline: parseTimeline(payload) ?? NO_TIMELINE,
    startedAtMs,
  };
}

/**
 * Reads a `stage.character.expression` payload. **`null` for an unknown emotion**, which
 * leaves the current face alone rather than quietly resetting it.
 */
export function toExpression(
  payload: Record<string, unknown>,
  startedAtMs: number,
): ExpressionState | null {
  const intent = parseExpression(payload);
  return intent ? { intent, startedAtMs } : null;
}
