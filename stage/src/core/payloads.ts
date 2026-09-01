/**
 * Reading `stage.*` payloads into the Stage's own types. **Pure functions.**
 *
 * `docs/contracts/wire.json` is authoritative for every name and value here (→ ADR-022);
 * `methods.ts` mirrors the method names, and these mirror the payload shapes.
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
 *
 * ## This file is the entry point; the readers live in `payloads/`
 *
 * They were one file until it reached 450 lines covering five unrelated payloads. Split
 * by **which question each answers** — the same division `store.ts` records for its own
 * split — and re-exported here so nothing that reads a payload has to know where the
 * reader moved to. `wire.test.ts` reads the constants through this file too.
 */

export {
  toCharacterModel,
  toExpression,
  toSpeech,
  toUserSaid,
} from "./payloads/character";
export { toInspectorSnapshot } from "./payloads/inspector";
export { asFiniteNumber, asNumber, asString, isRecord, oneOf, part } from "./payloads/read";
export { SETTINGS_SOURCES, toMicState, toSettingsSnapshot } from "./payloads/settings";
export {
  BOOT_PHASES,
  EMBEDDING_SETUP_STATES,
  ENGINE_RUNTIMES,
  LLM_SETUP_STATES,
  SETUP_COMPONENTS,
  STT_SETUP_STATES,
  TTS_SETUP_STATES,
  toEmbeddingSnapshot,
  toLlmSnapshot,
  toSetupPrompt,
  toSetupSnapshot,
  toSttSnapshot,
  toTtsSnapshot,
} from "./payloads/setup";
