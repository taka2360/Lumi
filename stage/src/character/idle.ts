/**
 * アイドルモーション — **純粋関数**。
 *
 * 時刻を引数で受け取る（テスト内で `performance.now()` を呼ばせない
 * → .claude/rules/tests.md「時刻は引数で渡す」）。
 *
 * Phase 0 では手続き的な呼吸と揺れだけ。モーションクリップの再生は Phase 1 以降。
 */

export interface IdlePose {
  /** 上下の揺れ（メートル）。 */
  offsetY: number;
  /** 左右の傾き（ラジアン）。 */
  tiltZ: number;
  /** 呼吸によるスケール。1.0 が基準。 */
  breathScale: number;
}

/** 呼吸の周期（秒）。ゆっくりにしないと「生きている」ではなく「震えている」に見える。 */
const BREATH_PERIOD_S = 4.0;
/** 体の揺れの周期（秒）。呼吸と割り切れない値にして、動きが同期しないようにする。 */
const SWAY_PERIOD_S = 6.7;

export function computeIdlePose(elapsedSeconds: number): IdlePose {
  const breath = Math.sin((elapsedSeconds / BREATH_PERIOD_S) * Math.PI * 2);
  const sway = Math.sin((elapsedSeconds / SWAY_PERIOD_S) * Math.PI * 2);
  return {
    offsetY: breath * 0.012,
    tiltZ: sway * 0.015,
    breathScale: 1 + breath * 0.004,
  };
}
