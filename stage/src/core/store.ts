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
export const SETTINGS_SOURCES: readonly SettingsSource[] = ["default", "file", "env"];

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

interface StageState {
  connected: boolean;
  settings: SettingsSnapshot | null;
  setup: SetupSnapshot;
  inspector: InspectorSnapshot | null;
  prompt: SetupPrompt | null;
  speech: Speech | null;
  userSaid: UserSaid | null;
  expression: ExpressionState | null;
  setConnected(connected: boolean): void;
  setSetup(snapshot: SetupSnapshot): void;
  setInspector(snapshot: InspectorSnapshot): void;
  setSettings(snapshot: SettingsSnapshot): void;
  setPrompt(prompt: SetupPrompt | null): void;
  setSpeech(speech: Speech | null): void;
  setUserSaid(said: UserSaid | null): void;
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
  inspector: null,
  settings: null,
  prompt: null,
  speech: null,
  userSaid: null,
  expression: null,
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
 * Reads a `stage.user.said` payload.
 *
 * **Empty text is still an event.** Core only sends this once STT produced something, so
 * an empty string here means the payload drifted — and a blank bubble says that far more
 * loudly than no bubble at all.
 */
export function toUserSaid(payload: Record<string, unknown>, startedAtMs: number): UserSaid {
  return {
    text: typeof payload.text === "string" ? payload.text : "",
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

/**
 * Reads a `stage.inspector.state` payload.
 *
 * **Everything is read defensively even though Core is a trusted peer.** The Inspector is
 * the view people reach for *while something is already wrong*; it throwing on a
 * half-written payload would take away the tool exactly when it is needed.
 */
export function toInspectorSnapshot(payload: Record<string, unknown>): InspectorSnapshot {
  const activities = Array.isArray(payload.activities) ? payload.activities : [];
  return {
    activities: activities.filter(isRecord).map(toInspectorActivity),
    latency: isRecord(payload.latency) ? toInspectorLatency(payload.latency) : null,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toInspectorActivity(raw: Record<string, unknown>): InspectorActivity {
  const cancellables = Array.isArray(raw.cancellables) ? raw.cancellables : [];
  return {
    id: asString(raw.id) ?? "",
    kind: asString(raw.kind) ?? "?",
    actor: asString(raw.actor) ?? "?",
    intent: asString(raw.intent) ?? "",
    state: asString(raw.state) ?? "?",
    priority: asNumber(raw.priority) ?? 0,
    foreground: raw.foreground === true,
    cancellables: cancellables.filter(isRecord).map((item) => ({
      label: asString(item.label) ?? "",
      contract: asString(item.contract) ?? "?",
      finished: item.finished === true,
    })),
  };
}

function toInspectorLatency(raw: Record<string, unknown>): InspectorLatency {
  // Core flattens the spans into the payload, so **anything numeric that is not one of
  // the summary fields is a span.** Adding a span on the Core side then needs no change here.
  const summary = new Set([
    "correlation_id",
    "measured_sum_ms",
    "total_ms",
    "unaccounted_ms",
    "completed",
  ]);
  const spans: Record<string, number> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!summary.has(key) && typeof value === "number") {
      spans[key] = value;
    }
  }
  return {
    correlation_id: asString(raw.correlation_id) ?? "",
    spans,
    measured_sum_ms: asNumber(raw.measured_sum_ms) ?? 0,
    total_ms: asNumber(raw.total_ms) ?? 0,
    unaccounted_ms: asNumber(raw.unaccounted_ms) ?? 0,
    completed: raw.completed === true,
  };
}

/**
 * Reads a `stage.settings.state` payload.
 *
 * **A value whose source cannot be read falls back to `default`.** Claiming it came from
 * the file would tell the user their setting is in effect when it may not be.
 */
export function toSettingsSnapshot(payload: Record<string, unknown>): SettingsSnapshot {
  const raw = isRecord(payload.values) ? payload.values : {};
  const values: Record<string, SettingValue> = {};
  for (const [key, entry] of Object.entries(raw)) {
    if (!isRecord(entry)) {
      continue;
    }
    values[key] = {
      value: asString(entry.value) ?? "",
      source: SETTINGS_SOURCES.find((candidate) => candidate === entry.source) ?? "default",
    };
  }
  return {
    version: asNumber(payload.version) ?? 0,
    unreadable: payload.unreadable === true,
    values,
  };
}
