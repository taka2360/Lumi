/**
 * Stage と Shell を繋ぐ React フック。
 *
 * - キャラクターの当たり判定領域を Shell に渡す（**変化したときだけ**）
 * - Shell から届くホバー状態を React に流す
 *
 * **ここに判断を書かない。** クリックスルーするかどうかを決めるのは Shell 側の純粋関数。
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { type CssRect, normalizeHitRects } from "./geometry";
import type { HoverState, PlatformShell } from "./PlatformShell";
import { createTauriPlatformShell, isTauri } from "./tauri";

/** Tauri の外（ブラウザで開いたとき）で使う何もしない実装。**黙って壊れないため**に明示的に置く。 */
const noopShell: PlatformShell = {
  setHitRegion: async () => {},
  onHoverState: async () => ({ dispose: () => {} }),
  startWindowDrag: async () => {},
  scaleWindow: async () => {},
};

export function getPlatformShell(): PlatformShell {
  return isTauri() ? createTauriPlatformShell() : noopShell;
}

/** ホバー状態を購読する。 */
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
 * 当たり判定領域を Shell に報告する関数を返す。
 *
 * アンマウント時は**空の領域**を送る（キャラクターが消えたらクリックスルーに戻す）。
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

/** ホイールの1目盛りあたりの拡大率。**小さめ**にする（一気に飛ぶと戻せない）。 */
const SCALE_STEP = 1.08;

/**
 * ウィンドウの移動と拡大縮小のハンドラ。**キャラクターの上でだけ使う。**
 *
 * 判断（どこまで小さく / 大きくできるか）は Shell 側にある
 * → docs/architecture/ui.md「ウィンドウの移動と大きさ」
 */
export function useWindowGestures() {
  const shell = useMemo(getPlatformShell, []);

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      // 左ボタンだけ。右クリックは将来メニューに使う。
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
