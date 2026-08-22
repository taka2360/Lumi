/**
 * `PlatformShell` — Shell as seen from the Stage. **Keeps an Electron escape route open** (docs/interfaces/shell.md).
 *
 * The Stage never directly knows Shell's implementation (Tauri / Electron). It only speaks through this interface.
 *
 * ## Why there are no OS privileges here
 *
 * docs/interfaces/shell.md's `PlatformShell` also lists `captureScreen` /
 * `injectInput` / `spawnSidecar`. Those are things **Core requests from Shell via
 * `os.*` (WS)**, and the Stage can't call them. **`stage.*` must never request OS
 * privileges** (docs/architecture/core.md §3). This interface mirrors **only the
 * part of Shell's full surface that's allowed to be exposed to the Stage.** Even
 * if the Stage is compromised, the most it should be able to do is "make a weird expression" (B1 / B2).
 *
 * ## Why there's no AI judgment here
 *
 * This path is `shell.*`. It only carries **things that should take under 1ms.**
 * The character's speech, expressions, and memory come from Core via `stage.*` (WS).
 */

/** A rectangle with its origin at the window's client area. **Physical pixels** (not CSS pixels). */
export interface HitRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Whether the cursor is inside the character's hit region. */
export type HoverState = "inside" | "outside";

export interface Disposable {
  dispose(): void;
}

export interface PlatformShell {
  /** Updates Shell-owned native labels. Carries presentation only, never AI judgment. */
  setLocale(locale: "ja" | "en"): Promise<void>;

  /** Converts a Core-provided Content Pack path into a WebView-fetchable asset URL. */
  toAssetUrl(path: string): string;

  /**
   * Hands the character's hit region to Shell.
   *
   * Shell uses this to toggle click-through. **The hit-test itself runs on the
   * Shell side** (round-tripping the cursor position at 60Hz couldn't stay within
   * `shell.*`'s 1ms budget).
   *
   * Passing an empty array means "no region" = always click-through.
   */
  setHitRegion(rects: HitRect[]): Promise<void>;

  /** Subscribes to hover-state changes. Called **only when it changes**. */
  onHoverState(callback: (state: HoverState) => void): Promise<Disposable>;

  /**
   * Grabs the window and moves it. **The OS decides the coordinates.**
   *
   * `setPosition` isn't exposed because it would let the Stage push the window off
   * screen (docs/interfaces/shell.md). Called the instant it's pressed.
   */
  startWindowDrag(): Promise<void>;

  /**
   * Changes the window's size **by a multiplier.**
   *
   * A multiplier is passed, not an absolute value. **The upper/lower bounds are
   * decided on the Shell side**; the Stage can only say "a bit bigger" (B1 / B2).
   */
  scaleWindow(factor: number): Promise<void>;

  /** Opens the bundled static credits and licenses window, or brings it forward. */
  openCredits(): Promise<void>;

  /** Opens the fixed Ollama official download page in the default browser. */
  openOllamaSite(): Promise<void>;

  /**
   * Quits Lumi.
   *
   * Exists for the [Quit] button on the setup screen (ADR-034). Anyone stopped there
   * has not met Lumi yet and **does not know it lives in the tray**, which until now
   * was the only way out (docs/architecture/ui.md "Tray menu").
   *
   * **No judgment travels with it.** No arguments, no branches — pressed means quit,
   * so Shell has nothing left to decide. What a compromised Stage gains is the ability
   * to close Lumi, which the user undoes by starting it again; it is not an OS
   * privilege and does not become one (docs/interfaces/shell.md).
   */
  quit(): Promise<void>;
}
