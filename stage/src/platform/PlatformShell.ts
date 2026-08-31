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

import type { PanelKind } from "../core/methods";

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

/**
 * Where Core is listening, and the token for **this window's role**.
 *
 * **Defined here rather than beside the WS client on purpose.** Shell is what knows this
 * — it starts Core, it holds the port, and it picks the token from the window's label
 * (ADR-042). Putting the type on the Core side would mean `platform/` importing `core/`
 * to describe a value that only ever travels the other way, and the point of this
 * interface is that it depends on nothing below it.
 */
export interface CoreEndpoint {
  port: number;
  token: string;
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
   * Asks Shell where Core is listening. `null` while Core is not up yet.
   *
   * **Shell chooses the token from the window's label**, not from anything the caller
   * says, so a window cannot ask for a role it was not given (ADR-042).
   */
  coreEndpoint(): Promise<CoreEndpoint | null>;

  /**
   * Subscribes to Core's endpoint changing — it changes because Core restarted on a new
   * port, so the answer is always "reconnect".
   *
   * **Returns something disposable, like `onHoverState`.** The WS client subscribes for
   * as long as it is open and unsubscribes when it closes; without a way to undo the
   * subscription, every reconnect would leave another listener behind.
   */
  onCoreEndpointChanged(callback: () => void): Promise<Disposable>;

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

  /**
   * Opens the bundled static operating guide, or brings it forward.
   *
   * **Static, like credits, and for a reason of its own**: the gestures it explains are
   * how the setup screen's controls are reached, and that screen is on display exactly
   * when Core has not come up (docs/architecture/ui.md §1).
   */
  openHelp(): Promise<void>;

  /**
   * Opens one of Lumi's own auxiliary windows — settings, inspector, memory — or brings
   * it forward (ADR-042).
   *
   * **The argument is one of three names, not a label or a URL.** Shell looks it up in a
   * fixed table and opens nothing for anything else, so this never becomes a way to make
   * arbitrary windows (docs/interfaces/shell.md).
   */
  openPanel(kind: PanelKind): Promise<void>;

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
