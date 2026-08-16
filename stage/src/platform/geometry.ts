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
