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
 * **The Stage never picks a path of its own.** `convertFileSrc` only rewrites the path into
 * an address the WebView can fetch; it grants nothing. A path outside the Content Pack is
 * refused by Shell, not by anything here.
 */

import { convertFileSrc } from "@tauri-apps/api/core";

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

export async function loadCharacter(source: ModelSource): Promise<LoadedCharacter> {
  if (!source.path) {
    // **Not an error.** A voice-only Content Pack is a legitimate Content Pack
    return { model: createPlaceholder(), fallbackReason: source.reason };
  }

  const url = convertFileSrc(source.path);
  try {
    // Checks existence first instead of waiting for GLTFLoader's exception on a 404.
    // **A refusal by Shell's scope arrives here as a failed response**, not as a throw
    const probe = await fetch(url, { method: "HEAD" });
    if (!probe.ok) {
      return {
        model: createPlaceholder(),
        fallbackReason: `VRM を読み込めません (${probe.status}): ${source.path}`,
      };
    }
    return { model: await loadVrm(url), fallbackReason: null };
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return { model: createPlaceholder(), fallbackReason: `VRM を読み込めません: ${reason}` };
  }
}
