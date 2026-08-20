/**
 * Loading the character. **Prefers VRM, falls back to the placeholder if absent.**
 *
 * The caller is told about a fallback via `fallbackReason`.
 * **Never silently degrades** (a guiding principle from docs/DESIGN.md), so the UI
 * shows why the placeholder is on screen.
 *
 * ## Who decides what (ADR-029)
 *
 * | | |
 * |---|---|
 * | **Core** | which model — it reads the Content Pack and sends the path |
 * | **Shell** | serving the bytes — reading a file is an OS privilege, and the path is checked against a scope |
 * | **Stage** | drawing it |
 *
 * **The Stage never picks a path of its own.** `PlatformShell.toAssetUrl` only rewrites the
 * path into an address the WebView can fetch; it grants nothing. A path outside the Content
 * Pack is refused by Shell, not by anything here.
 */

import { cachedLocale, hasMessage, type Locale, type MessageKey, translate } from "../i18n";
import { getPlatformShell } from "../platform/useStageShell";
import { createPlaceholder } from "./placeholder";
import type { CharacterModel } from "./types";
import { loadVrm } from "./vrm";

export interface LoadedCharacter {
  model: CharacterModel;
  /** The reason VRM couldn't be used. `null` when it worked. */
  fallbackReason: string | null;
}

/** What Core said about the model. `path: null` means the Content Pack ships none. */
export interface ModelSource {
  path: string | null;
  reason: string;
}

/**
 * Turns Core's reason **code** into something readable (ADR-036).
 *
 * **An unrecognised code is shown as-is**, never blanked and never rewritten to
 * "unknown error". Core may start sending a new reason before the Stage learns its
 * wording, and a raw `some_new_reason` on screen is a legible, searchable thing —
 * an empty placeholder caption is not. Same rule as `failureText` in `setup/status.ts`.
 */
export function modelReasonText(reason: string, locale: Locale = cachedLocale()): string {
  if (!reason) {
    return "";
  }
  const key = `character.model.${reason}` as MessageKey;
  return hasMessage(key) ? translate(locale, key) : reason;
}

export async function loadCharacter(source: ModelSource): Promise<LoadedCharacter> {
  const locale = cachedLocale();
  if (!source.path) {
    // **Not an error.** A voice-only Content Pack is a legitimate Content Pack
    return { model: createPlaceholder(), fallbackReason: modelReasonText(source.reason, locale) };
  }

  try {
    const url = getPlatformShell().toAssetUrl(source.path);
    // Checks existence first instead of waiting for GLTFLoader's exception on a 404.
    // **A refusal by Shell's scope arrives here as a failed response**, not as a throw
    const probe = await fetch(url, { method: "HEAD" });
    if (!probe.ok) {
      return {
        model: createPlaceholder(),
        fallbackReason: translate(locale, "character.load.status", {
          status: probe.status,
          path: source.path,
        }),
      };
    }
    return { model: await loadVrm(url), fallbackReason: null };
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return {
      model: createPlaceholder(),
      fallbackReason: translate(locale, "character.load.failed", { reason }),
    };
  }
}
