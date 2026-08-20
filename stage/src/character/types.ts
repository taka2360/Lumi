/**
 * The character's render target. Whether it's a VRM or a placeholder, the shape
 * seen from the Stage stays the same.
 *
 * The goal is **this staying intact when Live2D (Phase 9) is added.**
 * Expressions are received as "intent," not parameters (docs/interfaces/renderer.md, ADR-009).
 * Idle motion and lip sync are Phase 0; expressions arrived in Phase 1.
 */

import type { Object3D } from "three";

import type { ExpressionWeights } from "./expression";
import type { MouthWeights } from "./lipsync";

export type CharacterKind = "vrm" | "placeholder";

export interface CharacterModel {
  readonly kind: CharacterKind;
  /** The root added to the scene. */
  readonly object: Object3D;
  /** Called every frame. `deltaSeconds` is the elapsed time since the last frame. */
  update(deltaSeconds: number, elapsedSeconds: number): void;
  /**
   * Applies the mouth shape. **A Renderer that can't express it is free to do nothing**
   * (Core never branches on `capabilities()` → docs/interfaces/renderer.md).
   */
  applyMouth(weights: MouthWeights): void;
  /**
   * Applies the expression. **A Renderer with no such blend shape does nothing** —
   * the same contract as `applyMouth` (docs/interfaces/renderer.md).
   */
  applyExpression(weights: ExpressionWeights): void;
  /** Releases GPU resources. */
  dispose(): void;
}
