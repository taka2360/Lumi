/**
 * `PlatformShell` — Stage から見た Shell。**Electron 退避路の確保**（docs/interfaces/shell.md）。
 *
 * Stage は Shell の実装（Tauri / Electron）を直接知らない。この interface 越しにのみ話す。
 *
 * ## ここに OS 特権が無い理由
 *
 * docs/interfaces/shell.md の `PlatformShell` には `captureScreen` / `injectInput` /
 * `spawnSidecar` も載っている。それらは **Core が `os.*`（WS）で Shell に依頼するもの**であり、
 * Stage からは呼べない。**`stage.*` は絶対に OS 特権を要求しない**（docs/architecture/core.md §3）。
 * この interface は Shell の全体像のうち **Stage に露出してよい部分だけ**を写したものである。
 * Stage が乗っ取られても、できるのは「変な表情をする」までであるべき（B1 / B2）。
 *
 * ## ここに AI の判断が無い理由
 *
 * この経路は `shell.*`。**1ms 以下であるべきもの**しか通さない。
 * キャラクターの発話・表情・記憶は Core から `stage.*`（WS）で来る。
 */

/** ウィンドウのクライアント領域を原点とする矩形。**物理ピクセル**（CSS ピクセルではない）。 */
export interface HitRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** カーソルがキャラクターの当たり判定領域の中にいるか。 */
export type HoverState = "inside" | "outside";

export interface Disposable {
  dispose(): void;
}

export interface PlatformShell {
  /**
   * キャラクターの当たり判定領域を Shell に渡す。
   *
   * Shell はこれを使ってクリックスルーを切り替える。**判定は Shell 側で行う**
   * （60Hz のカーソル位置を往復させると `shell.*` の 1ms 予算を守れない）。
   *
   * 空配列を渡すと「領域なし」= 常にクリックスルーになる。
   */
  setHitRegion(rects: HitRect[]): Promise<void>;

  /** ホバー状態の変化を購読する。**変化したときだけ**呼ばれる。 */
  onHoverState(callback: (state: HoverState) => void): Promise<Disposable>;

  /**
   * ウィンドウを掴んで動かす。**座標は OS が決める。**
   *
   * `setPosition` を露出しないのは、Stage が画面外へウィンドウを追い出せるため
   * （docs/interfaces/shell.md）。押した瞬間に呼ぶ。
   */
  startWindowDrag(): Promise<void>;

  /**
   * ウィンドウの大きさを**倍率で**変える。
   *
   * 絶対値ではなく倍率を渡す。**上限・下限を決めるのは Shell 側**であり、
   * Stage は「もう少し大きく」としか言えない（B1 / B2）。
   */
  scaleWindow(factor: number): Promise<void>;
}
