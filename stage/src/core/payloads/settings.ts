/**
 * Reading `stage.settings.state` and `stage.audio.mic`.
 *
 * **Core resolves the settings; the Stage only shows them** — including which ones an
 * environment variable is overriding, since a setting that is being overridden without
 * saying so is worse than no setting at all.
 */

import type { MicState, SettingsSnapshot, SettingsSource, SettingValue } from "../store";
import { asNumber, asString, isRecord, oneOf } from "./read";

export const SETTINGS_SOURCES: readonly SettingsSource[] = ["default", "file", "env"];

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

/**
 * Whether the microphone is open, and whether the user muted it.
 *
 * **Both default to false on an unreadable payload.** For "open" that is the fail-closed
 * direction in the sense that matters here: the indicator claims Lumi is listening only
 * when Core said so, never because a field was missing.
 */
export function toMicState(payload: Record<string, unknown>): MicState {
  return { open: payload.open === true, muted: payload.muted === true };
}
