/**
 * Loading VRM. **The single point where a production model is received.**
 *
 * `@pixiv/three-vrm` is MIT. **The model file is never committed to the repository**
 * (`.gitignore`d), but it *is* in the distributable — the bundled default is redistributable
 * (docs/licensing.md §4.5) and ships inside the Content Pack.
 *
 * **This module doesn't know where the model is.** Core decides which one, Shell serves it,
 * and `loadCharacter` is handed a URL (ADR-029).
 */

import { type VRM, VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { browserLocale, translate } from "../i18n";
import { type ExpressionWeights, VRM_PRESETS } from "./expression";
import { computeIdlePose } from "./idle";
import type { MouthWeights, Viseme } from "./lipsync";
import type { CharacterModel } from "./types";

/** Viseme → VRM's standard expression name (docs/interfaces/renderer.md). */
const VRM_EXPRESSION: Readonly<Record<Viseme, string>> = {
  A: "aa",
  I: "ih",
  U: "ou",
  E: "ee",
  O: "oh",
};

/** 腕を下ろしたデフォルトポーズを適用 */
function applyDefaultPose(vrm: VRM): void {
  const humanoid = vrm.humanoid;
  if (!humanoid) {
    return;
  }
  const leftUpperArm = humanoid.getNormalizedBoneNode("leftUpperArm");
  const rightUpperArm = humanoid.getNormalizedBoneNode("rightUpperArm");
  if (leftUpperArm) {
    leftUpperArm.rotation.z = 1.25;
  }
  if (rightUpperArm) {
    rightUpperArm.rotation.z = -1.25;
  }
  humanoid.update();
}

export async function loadVrm(url: string): Promise<CharacterModel> {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));

  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm as VRM | undefined;
  if (!vrm) {
    throw new Error(translate(browserLocale(), "character.load.invalid", { url }));
  }

  // Drops unused joints (lowers render cost; three-vrm's recommended procedure).
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);

  // Faces it toward us. VRM 1.0 faces -Z.
  vrm.scene.rotation.y = Math.PI;

  // 初期姿勢として腕を下ろす
  applyDefaultPose(vrm);

  const baseY = vrm.scene.position.y;

  return {
    kind: "vrm",
    object: vrm.scene,
    update(delta: number, elapsed: number) {
      const pose = computeIdlePose(elapsed);
      vrm.scene.position.y = baseY + pose.offsetY;
      vrm.scene.rotation.z = pose.tiltZ;
      // Updates expressions, gaze, and physics. **`applyMouth`'s values get applied here.**
      vrm.update(delta);
    },
    applyMouth(weights: MouthWeights) {
      const expressions = vrm.expressionManager;
      if (!expressions) {
        // This model has no expressions. **Never silently pretends the mouth moved.**
        return;
      }
      for (const [viseme, name] of Object.entries(VRM_EXPRESSION)) {
        expressions.setValue(name, weights[viseme as Viseme]);
      }
    },
    applyExpression(weights: ExpressionWeights) {
      const expressions = vrm.expressionManager;
      if (!expressions) {
        return;
      }
      for (const preset of VRM_PRESETS) {
        // **A model missing this preset is not an error.** three-vrm ignores an unknown
        // name, which is exactly the "fall back on your own" the contract asks for
        expressions.setValue(preset, weights[preset]);
      }
    },
    dispose() {
      VRMUtils.deepDispose(vrm.scene);
    },
  };
}
