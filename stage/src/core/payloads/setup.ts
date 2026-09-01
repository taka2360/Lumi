/**
 * Reading the setup payloads — `stage.setup.state` and `stage.setup.prompt`.
 *
 * **The boot phase is Core's decision** (docs/architecture/setup.md §2b): the whole
 * snapshot arrives at once because the phase is a function of all the components, and
 * reading them from separate messages could not guarantee ordering.
 */

import type {
  BootPhase,
  EmbeddingSetupSnapshot,
  EmbeddingSetupState,
  EngineRuntime,
  LlmSetupSnapshot,
  LlmSetupState,
  SetupComponent,
  SetupModelOption,
  SetupPrompt,
  SetupSnapshot,
  SttSetupSnapshot,
  SttSetupState,
  TtsSetupSnapshot,
  TtsSetupState,
} from "../store";
import { asNumber, asString, isRecord, oneOf, part } from "./read";

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
export const SETUP_COMPONENTS: readonly SetupComponent[] = [
  "tts",
  "stt",
  "llm_model",
  "embedding",
  "all",
];

export const EMBEDDING_SETUP_STATES: readonly EmbeddingSetupState[] = [
  "unknown",
  "not_configured",
  "installing",
  "installed",
  "failed",
];

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
    embedding: toEmbeddingSnapshot(part(payload, "embedding")),
  };
}

/**
 * The embedding model's state (ADR-041).
 *
 * **Rounds an unknown value to `unknown`, not to `installed`.** Claiming a model is
 * present when the field could not be read would hide the one thing this screen exists to
 * offer.
 */
export function toEmbeddingSnapshot(payload: Record<string, unknown>): EmbeddingSetupSnapshot {
  return {
    state: oneOf(EMBEDDING_SETUP_STATES, payload.state, "unknown"),
    model: asString(payload.model),
    reason: asString(payload.reason),
    progress: asNumber(payload.progress),
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
    items: Array.isArray(payload.items)
      ? payload.items.filter(isRecord).flatMap((item) => {
          const name = asString(item.name);
          const size = asNumber(item.size_bytes);
          // **No fallback component.** Rounding an unknown name to `tts` would label a
          // download as something it is not, on the screen where the user consents to it.
          // A row nobody can identify is dropped, and the total below still says what
          // will be fetched.
          const part = SETUP_COMPONENTS.find((known) => known === item.component);
          return part && name && size !== null && size > 0
            ? [{ component: part, name, sizeBytes: size }]
            : [];
        })
      : [],
    // **Core's number, not a sum of what survived parsing.** If a row was dropped, the
    // list and the total disagree — and that is the honest thing to show, because the
    // bytes will still be fetched.
    totalBytes: asNumber(payload.total_bytes) ?? 0,
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
