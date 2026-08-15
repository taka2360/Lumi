/**
 * プレースホルダのキャラクター。
 *
 * **VRM が配置されていないときに、黙って何も出さないのを避けるためのもの**
 * （docs/licensing.md §7 の未確認 #5 が解消して既定モデルが決まるまでの間）。
 * 統合点（`CharacterModel`）は本番の VRM と同じなので、差し替えは `loadCharacter` の
 * 分岐1行で済む。
 */

import {
  CapsuleGeometry,
  Color,
  Group,
  Mesh,
  MeshStandardMaterial,
  type Object3D,
  SphereGeometry,
} from "three";

import { computeIdlePose } from "./idle";
import type { CharacterModel } from "./types";

const SKIN = new Color("#8fb4ff");
const ACCENT = new Color("#ffffff");

export function createPlaceholder(): CharacterModel {
  const root = new Group();
  const body = new Group();
  root.add(body);

  const material = new MeshStandardMaterial({ color: SKIN, roughness: 0.45, metalness: 0.0 });
  const accent = new MeshStandardMaterial({ color: ACCENT, roughness: 0.3, metalness: 0.0 });

  const torso = new Mesh(new CapsuleGeometry(0.16, 0.42, 8, 24), material);
  torso.position.y = 0.85;
  body.add(torso);

  const head = new Mesh(new SphereGeometry(0.16, 32, 24), material);
  head.position.y = 1.34;
  body.add(head);

  for (const side of [-1, 1]) {
    const arm = new Mesh(new CapsuleGeometry(0.05, 0.34, 6, 16), material);
    arm.position.set(side * 0.22, 0.88, 0);
    arm.rotation.z = side * 0.12;
    body.add(arm);

    const leg = new Mesh(new CapsuleGeometry(0.07, 0.42, 6, 16), material);
    leg.position.set(side * 0.09, 0.32, 0);
    body.add(leg);
  }

  // 目。VRM が来るまで「こちらを見ている」ことだけ表現する。
  for (const side of [-1, 1]) {
    const eye = new Mesh(new SphereGeometry(0.028, 16, 12), accent);
    eye.position.set(side * 0.06, 1.36, 0.145);
    body.add(eye);
  }

  const baseY = body.position.y;

  return {
    kind: "placeholder",
    object: root as Object3D,
    update(_delta: number, elapsed: number) {
      const pose = computeIdlePose(elapsed);
      body.position.y = baseY + pose.offsetY;
      body.rotation.z = pose.tiltZ;
      body.scale.setScalar(pose.breathScale);
    },
    dispose() {
      root.traverse((child) => {
        if (child instanceof Mesh) {
          child.geometry.dispose();
        }
      });
      material.dispose();
      accent.dispose();
    },
  };
}
