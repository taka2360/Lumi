/**
 * 座標変換 — **純粋関数**。Shell の実装に依存しないのでそのままテストできる。
 *
 * Shell は物理ピクセルで考える（OS のカーソル座標が物理ピクセルだから）。
 * WebView は CSS ピクセルで考える。**変換をここ1箇所に閉じ込める。**
 * 散らすと、混在 DPI のマルチモニタ（未確定事項 #15）で必ず壊れる。
 */

import type { HitRect } from "./PlatformShell";

export interface CssRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** CSS ピクセルの矩形を、ウィンドウのクライアント座標系の物理ピクセルに変換する。 */
export function toPhysicalRect(rect: CssRect, devicePixelRatio: number): HitRect {
  return {
    x: rect.x * devicePixelRatio,
    y: rect.y * devicePixelRatio,
    width: rect.width * devicePixelRatio,
    height: rect.height * devicePixelRatio,
  };
}

/**
 * 当たり判定に使う矩形へ整える。
 *
 * - 面積ゼロの矩形は落とす（Shell 側で判定不能な入力を送らない）
 * - 負のサイズは落とす（`getBoundingClientRect` は返さないが、算出値は返しうる）
 */
export function normalizeHitRects(rects: CssRect[], devicePixelRatio: number): HitRect[] {
  return rects
    .filter((r) => r.width > 0 && r.height > 0)
    .map((r) => toPhysicalRect(r, devicePixelRatio));
}
