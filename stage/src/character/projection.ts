/**
 * Projects the 3D hit region onto an on-screen rectangle — **pure functions**.
 *
 * The only thing passed to Shell is "where on screen the character is."
 * Received in a form independent of three.js (a list of NDC points), so it's
 * testable without starting the renderer.
 */

import type { CssRect } from "../platform/geometry";

export interface NdcPoint {
  /** -1 (left) to +1 (right) */
  x: number;
  /** -1 (bottom) to +1 (top) */
  y: number;
}

/**
 * Computes the on-screen rectangle (CSS pixels) enclosing the NDC points.
 *
 * `null` when there are no points. **Distinguishes "no region" from "a
 * zero-size region at the origin"** (the former keeps click-through, the latter
 * would become a 1px hit region).
 */
export function screenRectFromNdcPoints(
  points: readonly NdcPoint[],
  width: number,
  height: number,
): CssRect | null {
  if (points.length === 0 || width <= 0 || height <= 0) {
    return null;
  }

  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  for (const point of points) {
    const screenX = ((point.x + 1) / 2) * width;
    // NDC's y has +1 at the top. Screen coordinates are positive downward, so it's flipped.
    const screenY = ((1 - point.y) / 2) * height;
    minX = Math.min(minX, screenX);
    maxX = Math.max(maxX, screenX);
    minY = Math.min(minY, screenY);
    maxY = Math.max(maxY, screenY);
  }

  // Clips anything spilling outside the window (Shell judges by client coordinates).
  const left = Math.max(0, minX);
  const top = Math.max(0, minY);
  const right = Math.min(width, maxX);
  const bottom = Math.min(height, maxY);

  if (right <= left || bottom <= top) {
    return null;
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/** Whether the rectangle changed meaningfully. **No IPC is sent if it didn't.** */
export function hasMovedEnough(
  previous: CssRect | null,
  next: CssRect | null,
  thresholdPx = 1,
): boolean {
  if (previous === null || next === null) {
    return previous !== next;
  }
  return (
    Math.abs(previous.x - next.x) >= thresholdPx ||
    Math.abs(previous.y - next.y) >= thresholdPx ||
    Math.abs(previous.width - next.width) >= thresholdPx ||
    Math.abs(previous.height - next.height) >= thresholdPx
  );
}
