/**
 * Coordinate conversion — **pure functions**. Independent of Shell's
 * implementation, so it's directly testable.
 *
 * Shell thinks in physical pixels (because the OS's cursor coordinates are
 * physical pixels). The WebView thinks in CSS pixels. **The conversion is
 * confined to this one place.** Scattering it would inevitably break on a
 * mixed-DPI multi-monitor setup (open item #15).
 */

import type { HitRect } from "./PlatformShell";

export interface CssRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Converts a CSS-pixel rectangle into physical pixels in the window's client coordinate system. */
export function toPhysicalRect(rect: CssRect, devicePixelRatio: number): HitRect {
  return {
    x: rect.x * devicePixelRatio,
    y: rect.y * devicePixelRatio,
    width: rect.width * devicePixelRatio,
    height: rect.height * devicePixelRatio,
  };
}

/**
 * Prepares rectangles for use as the hit region.
 *
 * - Drops zero-area rectangles (never sends input the Shell side can't judge)
 * - Drops negative sizes (`getBoundingClientRect` never returns these, but a computed value could)
 */
export function normalizeHitRects(rects: CssRect[], devicePixelRatio: number): HitRect[] {
  return rects
    .filter((r) => r.width > 0 && r.height > 0)
    .map((r) => toPhysicalRect(r, devicePixelRatio));
}

/**
 * Whether a hit region differs from the one last handed to Shell.
 *
 * The character window recomputes its rectangles on every frame that moves anything, and
 * **most frames produce the identical region**. Comparing before sending keeps a 60Hz
 * render loop from becoming 60 IPC calls a second on the `shell.*` path, which is the one
 * with a 1ms budget (docs/architecture/ui.md §2).
 *
 * Compared field by field rather than by serialising: `NaN` never appears here (the rects
 * come from `getBoundingClientRect`), and this says what "the same region" means instead
 * of leaving it to a string.
 */
export function sameHitRegion(a: readonly CssRect[], b: readonly CssRect[]): boolean {
  if (a.length !== b.length) {
    return false;
  }
  return a.every((rect, index) => {
    const other = b[index];
    return (
      other !== undefined &&
      rect.x === other.x &&
      rect.y === other.y &&
      rect.width === other.width &&
      rect.height === other.height
    );
  });
}
