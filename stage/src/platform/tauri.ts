/**
 * `PlatformShell` の Tauri 実装。
 *
 * **Tauri に依存するコードはこのファイルだけ**にする。Electron へ退避するときに
 * 差し替えるのはここ1枚（docs/interfaces/shell.md）。
 */

import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";

import type { Disposable, HitRect, HoverState, PlatformShell } from "./PlatformShell";

/**
 * Shell の `shell.*` allowlist に登録されているコマンド名。
 *
 * **正は `docs/contracts/wire.json`**（→ ADR-022）。ただし Shell 側の実体は
 * `#[tauri::command]` が付いた**関数名**なので、突き合わせは Stage の片側だけである
 * （Shell で関名を変えてもテストは落ちない → docs/contracts/wire.md §4）。
 */
export const CMD_SET_HIT_REGION = "shell_hit_region_set";
export const CMD_DRAG_START = "shell_window_drag_start";
export const CMD_SCALE = "shell_window_scale";

/**
 * ホバー状態の通知イベント名。
 *
 * docs では `shell.hover.state` と書くが、**Tauri のイベント名にドットは使えない**
 * （英数字と `-` `/` `:` `_` のみ）。`.` を `:` に置き換えたものが線上の名前。
 * 対応する Shell 側の定数は `shell/src-tauri/src/hover.rs`。
 */
export const EVENT_HOVER_STATE = "shell:hover:state";

export function createTauriPlatformShell(): PlatformShell {
  return {
    async setHitRegion(rects: HitRect[]): Promise<void> {
      await invoke(CMD_SET_HIT_REGION, { rects });
    },

    async onHoverState(callback: (state: HoverState) => void): Promise<Disposable> {
      const unlisten = await getCurrentWebviewWindow().listen<HoverState>(
        EVENT_HOVER_STATE,
        (event) => callback(event.payload),
      );
      return { dispose: unlisten };
    },

    async startWindowDrag(): Promise<void> {
      await invoke(CMD_DRAG_START);
    },

    async scaleWindow(factor: number): Promise<void> {
      await invoke(CMD_SCALE, { factor });
    },
  };
}

/** Tauri の中で動いているか。ブラウザで `vite dev` を開いたときは false になる。 */
export function isTauri(): boolean {
  return "isTauri" in window && window.isTauri === true;
}
