/**
 * キャラクターの描画ホスト。
 *
 * - 透過背景（Stage は透過ウィンドウ。**背景を塗らない**）
 * - アイドルモーションでループ
 * - 描画結果から当たり判定領域を算出して Shell に渡す
 *   （docs/architecture/ui.md「ホバー検知の実装方針」）
 */

import { useEffect, useRef } from "react";
import {
  Box3,
  DirectionalLight,
  HemisphereLight,
  PerspectiveCamera,
  Scene,
  Vector3,
  WebGLRenderer,
} from "three";

import { useStageStore } from "../core/store";
import type { CssRect } from "../platform/geometry";
import { advanceMouth, MOUTH_CLOSED, type MouthWeights, visemeAt } from "./lipsync";
import { loadCharacter } from "./loadCharacter";
import { hasMovedEnough, type NdcPoint, screenRectFromNdcPoints } from "./projection";
import type { CharacterKind } from "./types";

/** 当たり判定の再計算間隔（フレーム）。毎フレーム bounding box を取り直すのは高い。 */
const BOUNDS_RECOMPUTE_INTERVAL = 10;

export interface CharacterStatus {
  kind: CharacterKind | null;
  fallbackReason: string | null;
}

export function CharacterCanvas({
  onStatus,
  onBounds,
}: {
  onStatus?: (status: CharacterStatus) => void;
  /** 画面上でキャラクターが占めている矩形。**当たり判定の材料**（判定は Shell 側）。 */
  onBounds?: (rect: CssRect | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    let disposed = false;
    const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setClearAlpha(0);

    const scene = new Scene();
    const camera = new PerspectiveCamera(28, 1, 0.1, 20);
    camera.position.set(0, 1.05, 2.6);
    camera.lookAt(0, 1.0, 0);

    scene.add(new HemisphereLight(0xffffff, 0x445566, 2.0));
    const key = new DirectionalLight(0xffffff, 1.4);
    key.position.set(1, 2, 2);
    scene.add(key);

    const resize = () => {
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (width === 0 || height === 0) {
        return;
      }
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    let animationFrame = 0;
    let disposeModel: (() => void) | null = null;

    void loadCharacter().then(({ model, fallbackReason }) => {
      if (disposed) {
        model.dispose();
        return;
      }
      scene.add(model.object);
      disposeModel = () => model.dispose();
      onStatus?.({ kind: model.kind, fallbackReason });

      const bounds = new Box3();
      const corner = new Vector3();
      let lastRect: CssRect | null = null;
      let frame = 0;
      let previousTime = performance.now();
      const startedAt = previousTime;
      let mouth: MouthWeights = MOUTH_CLOSED;

      const tick = (now: number) => {
        animationFrame = requestAnimationFrame(tick);
        const delta = Math.min((now - previousTime) / 1000, 0.1);
        previousTime = now;

        // **口を先に決めてから update する。** VRM は update の中で表情を反映するので、
        // 順序を逆にすると1フレーム遅れる。
        const speech = useStageStore.getState().speech;
        const target = speech ? visemeAt(speech.timeline, now - speech.startedAtMs) : null;
        mouth = advanceMouth(mouth, target, delta);
        model.applyMouth(mouth);

        model.update(delta, (now - startedAt) / 1000);
        renderer.render(scene, camera);

        frame += 1;
        if (frame % BOUNDS_RECOMPUTE_INTERVAL !== 0) {
          return;
        }
        bounds.setFromObject(model.object);
        if (bounds.isEmpty()) {
          return;
        }
        const points: NdcPoint[] = [];
        for (let i = 0; i < 8; i += 1) {
          corner.set(
            i & 1 ? bounds.max.x : bounds.min.x,
            i & 2 ? bounds.max.y : bounds.min.y,
            i & 4 ? bounds.max.z : bounds.min.z,
          );
          corner.project(camera);
          points.push({ x: corner.x, y: corner.y });
        }
        const rect = screenRectFromNdcPoints(points, canvas.clientWidth, canvas.clientHeight);
        if (hasMovedEnough(lastRect, rect)) {
          lastRect = rect;
          onBounds?.(rect);
        }
      };
      animationFrame = requestAnimationFrame(tick);
    });

    return () => {
      disposed = true;
      cancelAnimationFrame(animationFrame);
      observer.disconnect();
      disposeModel?.();
      renderer.dispose();
    };
  }, [onStatus, onBounds]);

  return <canvas ref={canvasRef} className="character" />;
}
