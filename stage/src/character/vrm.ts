/**
 * VRM の読み込み。**本番モデルの受け口はここ1箇所**。
 *
 * `@pixiv/three-vrm` は MIT。**モデルファイル自体は配布物に含めない**
 * （リポジトリでも `.gitignore` 済み → docs/licensing.md §2）。
 * 既定同梱モデルが決まったら `DEFAULT_VRM_URL` の指す先を Content Pack に変える。
 */

import { type VRM, VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { computeIdlePose } from "./idle";
import type { CharacterModel } from "./types";

/**
 * Phase 0 の暫定的な置き場所。ここに `.vrm` を置くと本番モデルとして読まれる。
 *
 * Phase 1 以降、モデルの選択は Content Pack の設定として **Core が決めて `stage.*` で配る**。
 * Stage が自分でパスを決めているのは Phase 0 の暫定処置である。
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

  // 使われないジョイントを落とす（描画コストを下げる、three-vrm の推奨手順）。
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);

  // 正面をこちらに向ける。VRM 1.0 は -Z 前方。
  vrm.scene.rotation.y = Math.PI;

  const baseY = vrm.scene.position.y;

  return {
    kind: "vrm",
    object: vrm.scene,
    update(delta: number, elapsed: number) {
      const pose = computeIdlePose(elapsed);
      vrm.scene.position.y = baseY + pose.offsetY;
      vrm.scene.rotation.z = pose.tiltZ;
      // 表情・視線・揺れものの更新。**リップシンクは Phase 0 の Step J で足す。**
      vrm.update(delta);
    },
    dispose() {
      VRMUtils.deepDispose(vrm.scene);
    },
  };
}
