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
