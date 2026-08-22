/**
 * The React hooks connecting the Stage to Shell.
 *
 * - Hands the character's hit region to Shell (**only when it changes**)
 * - Streams the hover state arriving from Shell into React
 *
 * **No decisions are made here.** Whether to click-through is decided by a pure function on the Shell side.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { type CssRect, normalizeHitRects } from "./geometry";
import type { HoverState, PlatformShell } from "./PlatformShell";
import { createTauriPlatformShell, isTauri } from "./tauri";

/** A no-op implementation used outside Tauri (when opened in a browser). Placed explicitly **so it never silently breaks**. */
const noopShell: PlatformShell = {
  setLocale: async () => {},
  toAssetUrl: (path) => path,
  setHitRegion: async () => {},
  onHoverState: async () => ({ dispose: () => {} }),
  startWindowDrag: async () => {},
  scaleWindow: async () => {},
  openCredits: async () => {},
  // Outside Tauri there is no process to end. **Left as a no-op rather than closing the
  // tab**: a browser-opened Stage is a development view, and taking the window away from
  // whoever opened it would be a surprise, not a service.
  quit: async () => {},
};

export function getPlatformShell(): PlatformShell {
  return isTauri() ? createTauriPlatformShell() : noopShell;
}

/** Subscribes to the hover state. */
export function useHoverState(): HoverState {
  const [hover, setHover] = useState<HoverState>("outside");

  useEffect(() => {
    const shell = getPlatformShell();
    let disposed = false;
    const subscription = shell.onHoverState((state) => {
      if (!disposed) {
        setHover(state);
      }
    });
    return () => {
      disposed = true;
      void subscription.then((s) => s.dispose());
    };
  }, []);

  return hover;
}

/**
 * Returns a function that reports the hit region to Shell.
 *
 * Sends **an empty region** on unmount (reverts to click-through once the character disappears).
 */
export function useHitRegionReporter(): (rects: CssRect[]) => void {
  const shell = useMemo(getPlatformShell, []);

  useEffect(() => {
    return () => {
      void shell.setHitRegion([]);
    };
  }, [shell]);

  return useCallback(
    (rects: CssRect[]) => {
      void shell.setHitRegion(normalizeHitRects(rects, window.devicePixelRatio));
    },
    [shell],
  );
}

/**
 * Returns a function that quits Lumi. Used by the setup screen's [Quit] (ADR-034)
 *
 * **Never wired to anything the character can reach.** The only caller is a button the
 * user presses on a screen that says Lumi has not started.
 */
export function useQuit(): () => void {
  const shell = useMemo(getPlatformShell, []);
  return useCallback(() => {
    void shell.quit();
  }, [shell]);
}

/** Returns a function that opens the bundled static credits and licenses window. */
export function useOpenCredits(onError?: (error: unknown) => void): () => void {
  const shell = useMemo(getPlatformShell, []);
  return useCallback(() => {
    void shell.openCredits().catch((error: unknown) => {
      // The caller owns the presentation of an actionable failure. Keeping the catch
      // here prevents a rejected Tauri invoke from becoming an unhandled rejection.
      onError?.(error);
    });
  }, [onError, shell]);
}

/** The scale factor per wheel notch. Kept **small** (jumping too far can't be undone). */
const SCALE_STEP = 1.08;

/** Elements whose own interaction must win over moving or resizing the whole window. */
const WINDOW_GESTURE_EXCLUSION =
  "button, a, input, select, textarea, [contenteditable='true'], [data-window-gesture='exclude']";

function isWindowGestureTarget(target: EventTarget | null): target is Element {
  return target instanceof Element && target.closest(WINDOW_GESTURE_EXCLUSION) === null;
}

/** Whether a pointer press is allowed to start native window dragging. Exported for regression tests. */
export function canStartWindowDrag(button: number, target: EventTarget | null): boolean {
  return button === 0 && isWindowGestureTarget(target);
}

/** Returns the bounded Shell request for a wheel interaction, or null over an excluded control. */
export function windowScaleFactor(deltaY: number, target: EventTarget | null): number | null {
  if (!isWindowGestureTarget(target)) {
    return null;
  }
  return deltaY < 0 ? SCALE_STEP : 1 / SCALE_STEP;
}

/**
 * Handlers for moving and resizing the window. Used over the character and over the
 * temporary boot/setup surface shown before the character is available.
 *
 * The decision (how small / large it's allowed to get) lives on the Shell side
 * → docs/architecture/ui.md "Moving and resizing the window"
 */
export function useWindowGestures() {
  const shell = useMemo(getPlatformShell, []);

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      // Buttons and selectable setup commands keep their native interaction. Everything
      // else on the visible surface acts like the character and can move the window.
      if (!canStartWindowDrag(event.button, event.target)) {
        return;
      }
      void shell.startWindowDrag();
    },
    [shell],
  );

  const onWheel = useCallback(
    (event: React.WheelEvent) => {
      const factor = windowScaleFactor(event.deltaY, event.target);
      if (factor === null) {
        return;
      }
      void shell.scaleWindow(factor);
    },
    [shell],
  );

  return { onPointerDown, onWheel };
}
