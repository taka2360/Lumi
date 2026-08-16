/**
 * 3D の当たり判定領域を画面上の矩形に落とす — **純粋関数**。
 *
 * Shell に渡すのは「キャラクターが画面のどこに居るか」だけ。
 * three.js に依存しない形（NDC の点列）で受け取るので、レンダラを起動せずにテストできる。
 */

import type { CssRect } from "../platform/geometry";

export interface NdcPoint {
  /** -1（左）〜 +1（右） */
  x: number;
  /** -1（下）〜 +1（上） */
  y: number;
}

/**
 * NDC の点列を囲む画面矩形（CSS ピクセル）を求める。
 *
 * 点が無いときは `null`。**「領域なし」と「原点にゼロサイズの領域」を区別する**
 * （前者はクリックスルー維持、後者は 1px の当たり判定になってしまう）。
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
    // NDC の y は上が +1。画面座標は下向きが正なので反転する。
    const screenY = ((1 - point.y) / 2) * height;
    minX = Math.min(minX, screenX);
    maxX = Math.max(maxX, screenX);
    minY = Math.min(minY, screenY);
    maxY = Math.max(maxY, screenY);
  }

  // ウィンドウの外にはみ出した分は落とす（Shell はクライアント座標で判定するため）。
  const left = Math.max(0, minX);
  const top = Math.max(0, minY);
  const right = Math.min(width, maxX);
  const bottom = Math.min(height, maxY);

  if (right <= left || bottom <= top) {
    return null;
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/** 矩形が実質的に変わったか。**変わっていなければ IPC を出さない。** */
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
