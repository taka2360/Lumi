/**
 * Reading `stage.*` payloads into the Stage's own types. **Pure functions.**
 *
 * `docs/contracts/wire.json` is authoritative for every name and value here (→ ADR-022);
 * `methods.ts` mirrors the method names, and this file mirrors the payload shapes.
 *
 * ## Everything is read defensively, and unknown values are never guessed at
 *
 * Core is a trusted peer, but **Lumi falls back to a safe default rather than
 * throwing** — the Inspector is the view people reach for while something is already
 * wrong, and a parse error there would take the tool away exactly when it is needed.
 *
 * The cost of that choice is that **drift is silent**: a value Core added and the Stage
 * does not know about simply becomes the fallback, with no error and no warning.
 * `wire.test.ts` is what makes it loud again.
 */

import type { ExpressionState } from "../character/expression";
import { parseExpression } from "../character/expression";
import { parseTimeline, type VisemeTimeline } from "../character/lipsync";
import type {
  BootPhase,
  CharacterModel,
  EngineRuntime,
  InspectorActivity,
  InspectorLatency,
  InspectorSnapshot,
  LlmSetupSnapshot,
  LlmSetupState,
  SettingsSnapshot,
  SettingsSource,
  SettingValue,
  SetupComponent,
  SetupModelOption,
  SetupPrompt,
  SetupSnapshot,
  Speech,
  SttSetupSnapshot,
  SttSetupState,
  TtsSetupSnapshot,
  TtsSetupState,
  UserSaid,
} from "./store";

/**
 * A timeline that moves nothing. Used when Core sends no `spans`.
 *
 * `visemeAt` returns `null` at every point, so **the mouth simply stays closed** —
 * which is what "no timeline" should mean.
 */
const NO_TIMELINE: VisemeTimeline = { spans: [], totalMs: 0 };

export const SETTINGS_SOURCES: readonly SettingsSource[] = ["default", "file", "env"];

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
  "model_installing",
  "model_failed",
];
export const STT_SETUP_STATES: readonly SttSetupState[] = [
  "unknown",
  "not_configured",
  "installing",
  "installed",
  "failed",
];
export const ENGINE_RUNTIMES: readonly EngineRuntime[] = ["stopped", "starting", "ready", "failed"];
export const BOOT_PHASES: readonly BootPhase[] = [
  "setup",
  "installing",
  "starting",
  "blocked",
  "ready",
];
export const SETUP_COMPONENTS: readonly SetupComponent[] = ["tts", "stt", "llm_model"];

/**
 * Picks `value` out of `candidates`, or falls back. **`fallback` is required.**
 *
 * Types vanish at runtime, so checking a received value against a union needs an
 * actual array. Making the fallback an argument rather than an optional means
 * **every caller has to state what an unknown value degrades to** — and every one of
 * them degrades to the safe side, never to the permissive one.
 */
function oneOf<T extends string>(candidates: readonly T[], value: unknown, fallback: T): T {
  return candidates.find((candidate) => candidate === value) ?? fallback;
}

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
    boot: oneOf(BOOT_PHASES, payload.boot, "starting"),
    tts: toTtsSnapshot(part(payload, "tts")),
    llm: toLlmSnapshot(part(payload, "llm")),
    stt: toSttSnapshot(part(payload, "stt")),
  };
}

export function toTtsSnapshot(payload: Record<string, unknown>): TtsSetupSnapshot {
  return {
    state: oneOf(TTS_SETUP_STATES, payload.state, "unknown"),
    runtime: oneOf(ENGINE_RUNTIMES, payload.runtime, "stopped"),
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
    state: oneOf(LLM_SETUP_STATES, payload.state, "unknown"),
    runtime: oneOf(ENGINE_RUNTIMES, payload.runtime, "stopped"),
    model: asString(payload.model),
    reason: asString(payload.reason),
    progress: asNumber(payload.progress),
    completed_bytes: asNumber(payload.completed_bytes),
    total_bytes: asNumber(payload.total_bytes),
  };
}

export function toSttSnapshot(payload: Record<string, unknown>): SttSetupSnapshot {
  return {
    state: oneOf(STT_SETUP_STATES, payload.state, "unknown"),
    model: asString(payload.model),
    reason: asString(payload.reason),
    progress: asNumber(payload.progress),
    runtime: oneOf(ENGINE_RUNTIMES, payload.runtime, "stopped"),
  };
}

/**
 * Reads a `stage.setup.prompt` payload. **An unknown component falls back to the engine.**
 *
 * Never left blank: a consent dialog that fails to say what it is asking about is worse
 * than one that names the more likely subject.
 */
export function toSetupPrompt(payload: Record<string, unknown>): SetupPrompt {
  const component = oneOf(SETUP_COMPONENTS, payload.component, "tts");
  const model = setupModelOption(payload.model);
  if (component === "llm_model" && model === null) {
    // A model prompt without a valid primary option cannot safely render a fallback or
    // answer Core without an identifier. Reject the command so the protocol reports drift.
    throw new Error("invalid_llm_model");
  }
  const alternatives = Array.isArray(payload.alternatives)
    ? payload.alternatives
        .filter(isRecord)
        .map(setupModelOption)
        .filter((item) => item !== null)
    : [];
  return {
    component,
    retry: payload.retry === true,
    reason: asString(payload.reason),
    model,
    alternatives,
  };
}

function setupModelOption(value: unknown): SetupModelOption | null {
  if (!isRecord(value)) return null;
  const model = asString(value.model);
  const displayName = asString(value.display_name);
  const sizeBytes = asNumber(value.size_bytes);
  if (!model || !displayName || sizeBytes === null || sizeBytes <= 0) return null;
  return {
    model,
    display_name: displayName,
    size_bytes: sizeBytes,
    installed: value.installed === true,
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
 * Reads a `stage.character.model` payload (ADR-029).
 *
 * **A missing path is a state, not a parse failure.** A Content Pack without a model is a
 * legitimate pack, and saying so is what keeps the placeholder from looking like a bug.
 * A pack that declares a model it doesn't ship never reaches here — Core refuses to load it.
 *
 * The payload also carries the model's credit, and this deliberately ignores it. The
 * credits window is static and doesn't connect to Core by design (docs/architecture/ui.md
 * "Why credits is not connected to Core"), so the credit obligation is met there, not here.
 * Parsing a field nothing renders would be an abstraction waiting for a use.
 */
export function toCharacterModel(payload: Record<string, unknown>): CharacterModel {
  const path = typeof payload.path === "string" && payload.path ? payload.path : null;
  const reason = typeof payload.reason === "string" ? payload.reason : "";
  if (!path) {
    return { path: null, format: "", reason: reason || "model_not_in_pack" };
  }
  return {
    path,
    format: typeof payload.format === "string" ? payload.format : "",
    reason: "",
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
      source: oneOf(SETTINGS_SOURCES, entry.source, "default"),
    };
  }
  return {
    version: asNumber(payload.version) ?? 0,
    unreadable: payload.unreadable === true,
    values,
  };
}
