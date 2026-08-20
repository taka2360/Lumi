/**
 * The placeholder character.
 *
 * **Exists so nothing is silently shown when no VRM is placed**
 * (until docs/licensing.md §7's open item #5 is resolved and a default model is
 * decided). The integration point (`CharacterModel`) is the same as the
 * production VRM, so swapping it out is a one-line branch in `loadCharacter`.
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

import type { ExpressionWeights, VrmPreset } from "./expression";
import { VRM_PRESETS } from "./expression";
import { computeIdlePose } from "./idle";
import type { MouthWeights } from "./lipsync";
import type { CharacterModel } from "./types";

const SKIN = new Color("#8fb4ff");
const ACCENT = new Color("#ffffff");

/**
 * What each preset tints the body toward. **A stand-in, not a design.**
 *
 * The placeholder exists so the pipeline is visible without a VRM
 * (Phase 0 verification step 6 did the same for lip sync). **An expression path that
 * shows nothing at all can't be told apart from one that isn't wired up.**
 */
const EXPRESSION_TINT: Readonly<Record<VrmPreset, Color>> = {
  neutral: SKIN,
  happy: new Color("#ffd76a"),
  sad: new Color("#6a86ff"),
  angry: new Color("#ff6a6a"),
  surprised: new Color("#b78dff"),
  relaxed: new Color("#7fe0c0"),
};

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

  // Eyes. Until the VRM arrives, only expresses "looking this way."
  for (const side of [-1, 1]) {
    const eye = new Mesh(new SphereGeometry(0.028, 16, 12), accent);
    eye.position.set(side * 0.06, 1.36, 0.145);
    body.add(eye);
  }

  // Mouth. **Needs to be visibly clear that lip sync is working** (Phase 0 verification step 6).
  const mouth = new Mesh(new SphereGeometry(0.032, 20, 14), accent);
  mouth.position.set(0, 1.28, 0.15);
  mouth.scale.set(1.0, 0.12, 0.5);
  body.add(mouth);

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
    applyMouth(weights: MouthWeights) {
      // Approximated with just two values: openness and width. Being a
      // placeholder, fidelity isn't needed, but it should still be visible that
      // **each vowel produces a different shape**.
      const open = Math.max(weights.A, weights.E, weights.I, weights.O, weights.U);
      const wide = Math.max(weights.I, weights.E);
      const round = Math.max(weights.U, weights.O);
      mouth.scale.set(1.0 + wide * 0.5 - round * 0.35, 0.12 + open * 1.1, 0.5);
    },
    applyExpression(weights: ExpressionWeights) {
      // Mixes each preset's tint in by its weight, starting from the base colour.
      const tinted = SKIN.clone();
      for (const preset of VRM_PRESETS) {
        if (preset !== "neutral" && weights[preset] > 0) {
          tinted.lerp(EXPRESSION_TINT[preset], weights[preset]);
        }
      }
      material.color.copy(tinted);
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
