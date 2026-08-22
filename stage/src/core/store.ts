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
 *
 * **Reading `stage.*` payloads lives in `payloads.ts`.** The two were one file until
 * it reached 550 lines; they answer different questions ("what is on screen" versus
 * "how is a frame from Core turned into it") and are read at different times.
 * `payloads.ts` imports these types — never the other way round.
 */

import { create } from "zustand";

import type { ExpressionState } from "../character/expression";
import type { VisemeTimeline } from "../character/lipsync";

/** The state of the engine **process**. A separate axis from installation state (`TtsSetupState`). */
export type EngineRuntime = "stopped" | "starting" | "ready" | "failed";

/**
 * The boot phase. **Core decides whether the character may be shown.**
 *
 * Defined in → docs/architecture/ui.md "Boot phases".
 * The Stage only switches screens based on this — **it never judges on its own**.
 *
 * `blocked` means setup is not finished: something the conversation needs is missing,
 * so no character appears and what is missing is shown instead (ADR-034). **Which
 * pieces are missing is read off the three component states**, never re-derived into a
 * separate verdict here.
 */
export type BootPhase = "setup" | "installing" | "starting" | "blocked" | "ready";

export type TtsSetupState =
  | "unknown"
  | "not_configured"
  | "detected"
  | "installing"
  | "installed"
  | "failed";

/** Whether Ollama is there. **Lumi never fetches it** (ADR-023), so there is no `installing`. */
export type LlmSetupState =
  | "unknown"
  | "not_configured"
  | "detected"
  | "model_missing"
  | "model_installing"
  | "model_failed";

/** Whether the speech model is there. Same shape as TTS **minus `detected`** — there is no
 * such thing as an STT model the user installed separately (docs/architecture/setup.md §2b). */
export type SttSetupState = "unknown" | "not_configured" | "installing" | "installed" | "failed";

/** Which component a question is about. **A question with no subject is not consent.** */
export type SetupComponent = "tts" | "stt" | "llm_model";

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

/** Same shape as Core's `LlmSetup.to_payload()`. */
export interface LlmSetupSnapshot {
  state: LlmSetupState;
  model: string | null;
  reason: string | null;
  progress: number | null;
  completed_bytes: number | null;
  total_bytes: number | null;
  runtime: EngineRuntime;
}

/** Same shape as Core's `SttSetup.to_payload()`. Acquisition and Provider load stay separate. */
export interface SttSetupSnapshot {
  state: SttSetupState;
  model: string | null;
  reason: string | null;
  progress: number | null;
  runtime: EngineRuntime;
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

/**
 * What Core heard the user say. **Kept until Lumi answers**, not on a timer.
 *
 * Core sends this before the Activity is even proposed, so a turn that goes nowhere
 * (proposal rejected, LLM down) still leaves the heard text on screen. **"It answered
 * something unrelated" and "it never heard me" look identical without this**, and the
 * first thing anyone needs to know is which one happened.
 */
export interface UserSaid {
  text: string;
  /** The time it was received (`performance.now()`). Restarts the entry animation. */
  startedAtMs: number;
}

/** That speech is in progress, and its mouth timeline. **Time advances on the Stage's own clock.** */
export interface Speech {
  text: string;
  timeline: VisemeTimeline;
  /** The time it was received (`performance.now()`). */
  startedAtMs: number;
}

export interface SetupPrompt {
  /** What is being asked about. **The panel's wording depends on it.** */
  component: SetupComponent;
  /** Whether this is a re-prompt after a failure. */
  retry: boolean;
  /** The previous failure reason (only populated when `retry`). */
  reason: string | null;
  model: SetupModelOption | null;
  alternatives: SetupModelOption[];
}

export interface SetupModelOption {
  model: string;
  display_name: string;
  size_bytes: number;
  installed: boolean;
}

/**
 * One Activity as the Inspector shows it. **Mirrors Core's `activity_payload`.**
 *
 * Several exist at once without violating Invariant 4 — `cancelling` and `suspended` are
 * not `running`. **Seeing that divergence is the point of the view.**
 */
export interface InspectorActivity {
  id: string;
  kind: string;
  actor: string;
  intent: string;
  state: string;
  priority: number;
  foreground: boolean;
  cancellables: { label: string; contract: string; finished: boolean }[];
}

/** The latency breakdown of the most recent turn. Mirrors Core's `TurnLatency.to_payload`. */
export interface InspectorLatency {
  correlation_id: string;
  spans: Record<string, number>;
  measured_sum_ms: number;
  total_ms: number;
  /** **The reserve's warning light.** Can be negative, and is not clamped. */
  unaccounted_ms: number;
  completed: boolean;
}

export interface InspectorSnapshot {
  activities: InspectorActivity[];
  latency: InspectorLatency | null;
}

/** Where an effective setting came from. **Values on the wire** → docs/contracts/wire.json. */
export type SettingsSource = "default" | "file" | "env";

export interface SettingValue {
  value: string;
  source: SettingsSource;
}

/**
 * The effective settings. **Core resolves them; the Stage only shows them** — including
 * which ones an environment variable is overriding, since a setting that is being
 * overridden without saying so is worse than no setting at all.
 */
export interface SettingsSnapshot {
  version: number;
  /** The file existed but could not be read. **Core will refuse to save over it.** */
  unreadable: boolean;
  values: Record<string, SettingValue>;
}

/**
 * Which model to draw, decided by Core from the Content Pack (ADR-029).
 *
 * `path` is an **absolute filesystem path, not a URL** — Core does not serve files. Turning
 * it into something the WebView can fetch is Shell's job, and the Stage asks Shell to do it.
 */
export interface CharacterModel {
  path: string | null;
  format: string;
  /** Why there is no model. Only set when `path` is `null`. **Shown, never swallowed.** */
  reason: string;
}

interface StageState {
  connected: boolean;
  settings: SettingsSnapshot | null;
  setup: SetupSnapshot;
  inspector: InspectorSnapshot | null;
  prompt: SetupPrompt | null;
  speech: Speech | null;
  userSaid: UserSaid | null;
  expression: ExpressionState | null;
  /** `null` = Core hasn't said yet. **Different from "there is no model"** (`path: null`) */
  model: CharacterModel | null;
  setConnected(connected: boolean): void;
  setSetup(snapshot: SetupSnapshot): void;
  setInspector(snapshot: InspectorSnapshot): void;
  setSettings(snapshot: SettingsSnapshot): void;
  setPrompt(prompt: SetupPrompt | null): void;
  setSpeech(speech: Speech | null): void;
  setUserSaid(said: UserSaid | null): void;
  setExpression(expression: ExpressionState | null): void;
  setModel(model: CharacterModel): void;
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
  progress: null,
  completed_bytes: null,
  total_bytes: null,
  runtime: "stopped",
};

const UNKNOWN_STT: SttSetupSnapshot = {
  state: "unknown",
  model: null,
  reason: null,
  progress: null,
  runtime: "stopped",
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
  inspector: null,
  settings: null,
  prompt: null,
  speech: null,
  userSaid: null,
  expression: null,
  model: null,
  setConnected: (connected) => set({ connected }),
  setSetup: (setup) => set({ setup }),
  setInspector: (inspector) => set({ inspector }),
  setSettings: (settings) => set({ settings }),
  setPrompt: (prompt) => set({ prompt }),
  // **Lumi starting to speak clears what the user said.** That is the turn changing hands,
  // and it comes from a Core event rather than a Stage-side timer — the Stage decides nothing
  setSpeech: (speech) => set(speech ? { speech, userSaid: null } : { speech }),
  setUserSaid: (userSaid) => set({ userSaid }),
  setExpression: (expression) => set({ expression }),
  setModel: (model) => set({ model }),
}));
