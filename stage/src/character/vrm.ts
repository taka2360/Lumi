/**
 * Loading VRM. **The single point where a production model is received.**
 *
 * `@pixiv/three-vrm` is MIT. **The model file itself is never included in the
 * distributable** (already `.gitignore`d in the repo too → docs/licensing.md §2).
 * Once a default bundled model is decided, change what `DEFAULT_VRM_URL` points at to the Content Pack.
 */

import { type VRM, VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

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

/**
 * The provisional location for Phase 0. Placing a `.vrm` here loads it as the production model.
 *
 * From Phase 1 onward, model selection is a Content Pack setting **decided by
 * Core and broadcast via `stage.*`.** The Stage deciding its own path is Phase 0's provisional measure.
 */
export const DEFAULT_VRM_URL = "/character.vrm";

export async function loadVrm(url: string): Promise<CharacterModel> {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));

  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm as VRM | undefined;
  if (!vrm) {
    throw new Error(`VRM として読めない: ${url}`);
  }

  // Drops unused joints (lowers render cost; three-vrm's recommended procedure).
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);

  // Faces it toward us. VRM 1.0 faces -Z.
  vrm.scene.rotation.y = Math.PI;

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
    dispose() {
      VRMUtils.deepDispose(vrm.scene);
    },
  };
}
