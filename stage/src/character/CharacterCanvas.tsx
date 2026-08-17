/**
 * The character's rendering host.
 *
 * - Transparent background (the Stage is a transparent window. **Never paints a background**)
 * - Loops on idle motion
 * - Computes the hit region from the render output and hands it to Shell
 *   (docs/architecture/ui.md "Hover-detection implementation approach")
 */

import { useEffect, useRef } from "react";
import {
  AmbientLight,
  Box3,
  DirectionalLight,
  NoToneMapping,
  PerspectiveCamera,
  Scene,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
} from "three";

import { useStageStore } from "../core/store";
import type { CssRect } from "../platform/geometry";
import {
  advanceExpression,
  EXPRESSION_NEUTRAL,
  type ExpressionWeights,
  targetWeights,
} from "./expression";
import { advanceMouth, MOUTH_CLOSED, type MouthWeights, visemeAt } from "./lipsync";
import { loadCharacter } from "./loadCharacter";
import { hasMovedEnough, type NdcPoint, screenRectFromNdcPoints } from "./projection";
import type { CharacterKind } from "./types";

/** Recomputation interval for the hit region (in frames). Recomputing the bounding box every frame is expensive. */
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
  /** The on-screen rectangle the character occupies. **The material for hit-testing** (the test itself runs in Shell). */
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
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.toneMapping = NoToneMapping;

    const scene = new Scene();
    const camera = new PerspectiveCamera(28, 1, 0.1, 20);
    camera.position.set(0, 1.05, 2.6);
    camera.lookAt(0, 1.0, 0);

    // アニメ調モデルの陰影を綺麗に出すため均一な白色の環境光と正面寄りの平行光を設定
    scene.add(new AmbientLight(0xffffff, 0.8));
    const key = new DirectionalLight(0xffffff, 1.2);
    key.position.set(0.25, 1.0, 1.5).normalize();
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
      let face: ExpressionWeights = EXPRESSION_NEUTRAL;

      const tick = (now: number) => {
        animationFrame = requestAnimationFrame(tick);
        const delta = Math.min((now - previousTime) / 1000, 0.1);
        const previousFrameAt = previousTime;
        previousTime = now;

        // **Decides the mouth before calling update.** VRM applies expressions
        // inside update, so reversing the order would lag by one frame.
        const speech = useStageStore.getState().speech;
        const target = speech ? visemeAt(speech.timeline, now - speech.startedAtMs) : null;
        mouth = advanceMouth(mouth, target, delta);
        model.applyMouth(mouth);

        // Same rule as the mouth: **Core states the target once, the Stage advances time.**
        const expression = useStageStore.getState().expression;
        face = advanceExpression(
          face,
          targetWeights(expression, now),
          now - previousFrameAt,
          expression?.intent.blendMs ?? 0,
        );
        model.applyExpression(face);

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
      // **Reports that it disappeared.** Without this, a hit region would linger
      // where the character no longer is, and click-through would stop working there.
      onBounds?.(null);
    };
  }, [onStatus, onBounds]);

  return <canvas ref={canvasRef} className="character" />;
}
