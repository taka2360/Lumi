/**
 * キャラクターの読み込み。**VRM を優先し、無ければプレースホルダに落とす。**
 *
 * 落ちたことは呼び出し側に `fallbackReason` で伝える。
 * **黙って劣化しない**（docs/DESIGN.md の進め方の原則）ため、UI 側で「VRM 未配置」と表示する。
 */

import { createPlaceholder } from "./placeholder";
import type { CharacterModel } from "./types";
import { DEFAULT_VRM_URL, loadVrm } from "./vrm";

export interface LoadedCharacter {
  model: CharacterModel;
  /** VRM を使えなかった理由。使えたときは `null`。 */
  fallbackReason: string | null;
}

export async function loadCharacter(url: string = DEFAULT_VRM_URL): Promise<LoadedCharacter> {
  try {
    // 404 のときに GLTFLoader の例外を待たず、先に存在を確かめる。
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
