/**
 * Stage — **表現のみ**。ビジネスロジックを持たない。
 *
 * 判定基準: ストアから読める値は、すべて Core が `stage.*` で配信したもの。
 * Stage が自分で計算して状態を作っていたら、ロジックが漏れている。
 * → docs/architecture/ui.md §2
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { CharacterCanvas, type CharacterStatus } from "./character/CharacterCanvas";
import { useCoreConnection } from "./core/useCoreConnection";
import type { CssRect } from "./platform/geometry";
import { useHitRegionReporter, useHoverState } from "./platform/useStageShell";
import { SetupPanel } from "./setup/SetupPanel";

/** 要素の画面上の矩形を追う。レイアウトが変わるたびに更新する。 */
function useElementRect(element: HTMLElement | null): CssRect | null {
  const [rect, setRect] = useState<CssRect | null>(null);

  useEffect(() => {
    if (!element) {
      setRect(null);
      return;
    }
    const measure = () => {
      const box = element.getBoundingClientRect();
      setRect({ x: box.x, y: box.y, width: box.width, height: box.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [element]);

  return rect;
}

export function App() {
  useCoreConnection();

  const hover = useHoverState();
  const reportHitRegion = useHitRegionReporter();

  const [status, setStatus] = useState<CharacterStatus>({ kind: null, fallbackReason: null });
  const onStatus = useCallback((next: CharacterStatus) => setStatus(next), []);

  const [characterRect, setCharacterRect] = useState<CssRect | null>(null);
  const onBounds = useCallback((rect: CssRect | null) => setCharacterRect(rect), []);

  // パネルの上ではクリックスルーを解除する必要がある。
  // **キャラクターとパネルの和**を当たり判定として渡す（docs/architecture/ui.md）。
  const [panel, setPanel] = useState<HTMLDivElement | null>(null);
  const panelRect = useElementRect(panel);

  const lastReported = useRef<string>("");
  useEffect(() => {
    const rects = [characterRect, panelRect].filter((rect): rect is CssRect => rect !== null);
    const signature = JSON.stringify(rects);
    if (signature === lastReported.current) {
      return;
    }
    lastReported.current = signature;
    reportHitRegion(rects);
  }, [characterRect, panelRect, reportHitRegion]);

  return (
    <div className={hover === "inside" ? "stage stage--hover" : "stage"}>
      <CharacterCanvas onStatus={onStatus} onBounds={onBounds} />
      <div className="overlay" ref={setPanel}>
        <SetupPanel />
        {/* **黙って劣化しない。** 本番の VRM ではなくプレースホルダで動いていることを見せる。 */}
        {status.fallbackReason && <p className="notice">{status.fallbackReason}</p>}
      </div>
    </div>
  );
}
