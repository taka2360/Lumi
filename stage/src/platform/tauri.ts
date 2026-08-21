/**
 * The Tauri implementation of `PlatformShell`.
 *
 * **Code depending on Tauri lives only in this file.** If ever migrating to
 * Electron, this is the only file that needs replacing (docs/interfaces/shell.md).
 */

import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";

import type { Disposable, HitRect, HoverState, PlatformShell } from "./PlatformShell";

/**
 * Command names registered in Shell's `shell.*` allowlist.
 *
 * **`docs/contracts/wire.json` is authoritative** (→ ADR-022). But the actual
 * entity on the Shell side is the **function name** annotated with
 * `#[tauri::command]`, so cross-checking only happens on the Stage side (renaming
 * the function in Shell doesn't fail any test → docs/contracts/wire.md §4).
 */
export const CMD_SET_HIT_REGION = "shell_hit_region_set";
export const CMD_DRAG_START = "shell_window_drag_start";
export const CMD_SCALE = "shell_window_scale";
export const CMD_SET_LOCALE = "shell_locale_set";
export const CMD_OPEN_CREDITS = "shell_credits_open";
export const CMD_QUIT = "shell_app_quit";

/**
 * The event name for hover-state notifications.
 *
 * The docs write this as `shell.hover.state`, but **Tauri event names can't
 * contain dots** (only alphanumerics and `-` `/` `:` `_`). The wire name replaces
 * `.` with `:`. The corresponding constant on the Shell side is in `shell/src-tauri/src/hover.rs`.
 */
export const EVENT_HOVER_STATE = "shell:hover:state";

export function createTauriPlatformShell(): PlatformShell {
  return {
    async setLocale(locale: "ja" | "en"): Promise<void> {
      await invoke(CMD_SET_LOCALE, { locale });
    },

    toAssetUrl(path: string): string {
      return convertFileSrc(path);
    },

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

    async openCredits(): Promise<void> {
      await invoke(CMD_OPEN_CREDITS);
    },

    async quit(): Promise<void> {
      await invoke(CMD_QUIT);
    },
  };
}

/** Whether this is running inside Tauri. False when opened in a browser via `vite dev`. */
export function isTauri(): boolean {
  return "isTauri" in window && window.isTauri === true;
}
