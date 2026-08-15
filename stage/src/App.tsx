/**
 * Stage — **表現のみ**。ビジネスロジックを持たない。
 *
 * 判定基準: ストアから読める値は、すべて Core が `stage.*` で配信したもの。
 * Stage が自分で計算して状態を作っていたら、ロジックが漏れている。
 * → docs/architecture/ui.md §2
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { CharacterCanvas, type CharacterStatus } from "./character/CharacterCanvas";
import { useStageStore } from "./core/store";
import { useCoreConnection } from "./core/useCoreConnection";
import type { CssRect } from "./platform/geometry";
import { useHitRegionReporter, useHoverState, useWindowGestures } from "./platform/useStageShell";
import { BootScreen } from "./setup/BootScreen";
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
  const gestures = useWindowGestures();

  // **キャラクターを出してよいかは Core が決める**（docs/architecture/ui.md「起動フェーズ」）。
  const tts = useStageStore((state) => state.tts);
  const connected = useStageStore((state) => state.connected);
  const prompt = useStageStore((state) => state.prompt);
  const phase = tts.boot;
  const showCharacter = connected && phase === "ready";

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
      {/* キャラクターを掴んでウィンドウを動かす面。キーボードからは到達させない
          （ウィンドウの移動と大きさは OS の作法に従うもので、Stage の UI ではない）。 */}
      {showCharacter && (
        <div
          className="stage__grab"
          onPointerDown={gestures.onPointerDown}
          onWheel={gestures.onWheel}
        >
          <CharacterCanvas onStatus={onStatus} onBounds={onBounds} />
        </div>
      )}
      <div className="overlay" ref={setPanel}>
        {/* 準備中は、キャラクターの代わりに何が起きているかを出す。
            **出すのは常に1つだけ**（docs/architecture/ui.md「起動フェーズ」）。
            ローディングとパネルを並べると、同じ状況が二重に書かれる。 */}
        {showCharacter || prompt ? <SetupPanel /> : <BootScreen tts={tts} connected={connected} />}
        {/* **黙って劣化しない。** 本番の VRM ではなくプレースホルダで動いていることを見せる。 */}
        {status.fallbackReason && <p className="notice">{status.fallbackReason}</p>}
      </div>
    </div>
  );
}
