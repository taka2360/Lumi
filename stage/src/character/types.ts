/**
 * キャラクターの描画対象。VRM でもプレースホルダでも、Stage から見た形は同じにする。
 *
 * **Live2D（Phase 9）を足すときにここが壊れないこと**が目的。
 * 表情はパラメータではなく「意図」で受け取る（docs/interfaces/renderer.md, ADR-009）。
 * Phase 0 では表情もリップシンクも無いので、まだ `update` と `object` だけ。
 */

import type { Object3D } from "three";

export type CharacterKind = "vrm" | "placeholder";

export interface CharacterModel {
  readonly kind: CharacterKind;
  /** シーンに追加する root。 */
  readonly object: Object3D;
  /** 毎フレーム呼ぶ。`deltaSeconds` は前フレームからの経過秒。 */
  update(deltaSeconds: number, elapsedSeconds: number): void;
  /** GPU リソースを解放する。 */
  dispose(): void;
}
