/**
 * Loading the character. **Prefers VRM, falls back to the placeholder if absent.**
 *
 * The caller is told about a fallback via `fallbackReason`.
 * **Never silently degrades** (a guiding principle from docs/DESIGN.md), so the UI
 * shows "VRM not placed."
 */

import { createPlaceholder } from "./placeholder";
import type { CharacterModel } from "./types";
import { DEFAULT_VRM_URL, loadVrm } from "./vrm";

export interface LoadedCharacter {
  model: CharacterModel;
  /** The reason VRM couldn't be used. `null` when it worked. */
  fallbackReason: string | null;
}

export async function loadCharacter(url: string = DEFAULT_VRM_URL): Promise<LoadedCharacter> {
  try {
    // Checks existence first instead of waiting for GLTFLoader's exception on a 404.
    const probe = await fetch(url, { method: "HEAD" });
    if (!probe.ok) {
      return {
        model: createPlaceholder(),
        fallbackReason: `VRM が見つかりません (${probe.status}): ${url}`,
      };
    }
    return { model: await loadVrm(url), fallbackReason: null };
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return { model: createPlaceholder(), fallbackReason: `VRM を読み込めません: ${reason}` };
  }
}
