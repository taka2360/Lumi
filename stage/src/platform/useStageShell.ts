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
  setHitRegion: async () => {},
  onHoverState: async () => ({ dispose: () => {} }),
  startWindowDrag: async () => {},
  scaleWindow: async () => {},
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

/** The scale factor per wheel notch. Kept **small** (jumping too far can't be undone). */
const SCALE_STEP = 1.08;

/**
 * Handlers for moving and resizing the window. **Used only over the character.**
 *
 * The decision (how small / large it's allowed to get) lives on the Shell side
 * → docs/architecture/ui.md "Moving and resizing the window"
 */
export function useWindowGestures() {
  const shell = useMemo(getPlatformShell, []);

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      // Left button only. Right-click is reserved for a future menu.
      if (event.button !== 0) {
        return;
      }
      void shell.startWindowDrag();
    },
    [shell],
  );

  const onWheel = useCallback(
    (event: React.WheelEvent) => {
      void shell.scaleWindow(event.deltaY < 0 ? SCALE_STEP : 1 / SCALE_STEP);
    },
    [shell],
  );

  return { onPointerDown, onWheel };
}
