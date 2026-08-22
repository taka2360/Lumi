# Interface: PlatformShell

> Tauri 2 が破綻した場合の Electron 退避路。Phase 0 で定義し、Tauri 実装を作る。

親: [DESIGN.md](../DESIGN.md) / 関連: [../architecture/ui.md](../architecture/ui.md) / [ADR-001](../decisions/ADR-001-desktop-shell-tauri.md)

---

## なぜ抽象化するのか

Tauri 2 には未検証のリスクがある（R2）。

| 課題 | 状況 |
|---|---|
| **クリックスルー + ホバー検知** | `setIgnoreCursorEvents` はあるが、Electron の `setIgnoreMouseEvents(true, {forward: true})` 相当が無い。**Rust 側で Win32 カーソル監視を自前実装する必要がある** |
| Windows での透過 + 常時最前面 | 実績はあるが、Lumi の要件（非フォーカス表示 + 常時最前面 + 透過の同時成立）での検証が必要 |
| Python サイドカーの同梱 | `externalBin` で可能だが、サイズが未知（R1） |

**Phase 0 のスパイクで破綻したら Shell だけ差し替えられる**ようにしておく。抽象化コストは小さく、リスクは大きいため、この抽象は正当化される。

### 抽象化しないもの

**Stage（WebView 内）は抽象化しない。** React + TypeScript のコードは Tauri でも Electron でもそのまま動く。差し替えるのは Shell 側だけ。

---

## Interface

```typescript
interface PlatformShell {
  // ── ウィンドウ ─────────────────────────────
  createWindow(spec: WindowSpec): Promise<WindowHandle>
  openCredits(): Promise<void>                         // 静的な credits ウィンドウを開く / 前面へ出す
  openOllamaSite(): Promise<void>                      // 固定の Ollama 公式ダウンロードページを既定ブラウザで開く
  setTransparent(w: WindowHandle, on: boolean): Promise<void>
  setAlwaysOnTop(w: WindowHandle, on: boolean): Promise<void>
  setClickThrough(w: WindowHandle, on: boolean): Promise<void>
  setHitRegion(w: WindowHandle, region: HitRegion): Promise<void>
  showInactive(w: WindowHandle): Promise<void>     // フォーカスを奪わない表示
  setContentProtection(w: WindowHandle, on: boolean): Promise<void>
  setPosition(w: WindowHandle, x: number, y: number): Promise<void>
  getPosition(w: WindowHandle): Promise<Point>
  startDragging(w: WindowHandle): Promise<void>          // 掴んで動かす。OS に委ねる
  scaleWindow(w: WindowHandle, factor: number): Promise<void>  // 倍率。**クランプは Shell 側**

  // ── Stage asset delivery ───────────────────
  toAssetUrl(path: string): string                    // Content Pack の scope 内だけ

  // ── 入力 ───────────────────────────────────
  onCursorMove(cb: (p: Point) => void): Disposable
  registerHotkey(accel: string, cb: () => void): Promise<Disposable>

  // ── トレイ ─────────────────────────────────
  setTrayMenu(items: TrayItem[]): Promise<void>
  setLocale(locale: "ja" | "en"): Promise<void>

  // ── プロセス ───────────────────────────────
  quit(): Promise<void>                               // Lumi 全体を終了する

  // ── OS 特権（Core からの os.* を受けて実行）────
  captureScreen(spec: CaptureSpec): Promise<ImageData>
  injectInput(spec: InputSpec): Promise<void>
  launchProcess(spec: ProcessSpec): Promise<ProcessHandle>

  // ── サイドカー ─────────────────────────────
  spawnSidecar(spec: SidecarSpec): Promise<SidecarHandle>
  onSidecarExit(h: SidecarHandle, cb: (code: number) => void): Disposable
}
```

---

## `setClickThrough` と `onCursorMove` — 最大の差異点

### Electron

```ts
window.setIgnoreMouseEvents(true, { forward: true })
// クリックは貫通しつつ、mousemove は届く
```

### Tauri 2

```ts
await window.setIgnoreCursorEvents(true)
// クリックは貫通するが、mousemove も届かない
```

**`forward: true` 相当が無い。** これが Lumi の要件（キャラクターにカーソルを乗せたら反応する）と衝突する。

### Tauri 実装の方針〔Phase 0 で実装・実測済み〕

```
Stage が setHitRegion(rects) で当たり判定領域を渡す
  ↓
Rust 側で ~60Hz でカーソル位置を取得（GetCursorPos 相当）
  ↓ 判定も Shell 側（純粋関数）
領域内 → setClickThrough(false) + onHoverState("inside")
領域外 → setClickThrough(true)  + onHoverState("outside")
```

**当初案（カーソル位置を Stage に送って Stage が比較する）を採らない。**
60Hz の往復は `shell.*` の「1ms 以下であるべきもの」を守れず、Stage が固まると
クリックスルーも固まる。Stage が渡すのは**領域だけ**にする。
→ [../architecture/ui.md](../architecture/ui.md)「ホバー検知の実装方針」

**実測**: 16ms 周期のポーリングで 1コアの約 2.8%（debug ビルド）。
→ [../measurements/phase0.md](../measurements/phase0.md)

破綻した場合の代替:
1. ローレベルマウスフック（`SetWindowsHookEx`）に切り替える
2. `PlatformShell` を Electron 実装に差し替える

### Stage に露出する範囲

上の `PlatformShell` は **Shell 全体の姿**であり、そのすべてが Stage から呼べるわけではない。

| 分類 | 呼ぶ主体 | 経路 |
|---|---|---|
| `setHitRegion` / `onHoverState` / ウィンドウ操作 / `openCredits` / `openOllamaSite` / `quit` | Stage | `shell.*`（Tauri IPC） |
| `toAssetUrl` | Stage | PlatformShell adapter（Tauri asset protocol） |
| `captureScreen` / `injectInput` / `launchProcess` / `spawnSidecar` | **Core** | `os.*`（WS） |

**Stage 側の TypeScript の `PlatformShell` には OS 特権を載せない。**
`stage.*` は絶対に OS 特権を要求しない、という規則を型で守るため
（実装: `stage/src/platform/PlatformShell.ts`）。

#### `quit` を Stage に露出してよい理由〔Phase 1・[ADR-034](../decisions/ADR-034-gate-startup-on-complete-setup.md)〕

セットアップ未完了で止まった画面（`boot: blocked`）に **[終了]** を置くために要る。
そこまで到達したユーザーは、**トレイに Lumi が居ることをまだ知らない**（[../architecture/ui.md](../architecture/ui.md)「トレイメニュー」）。

**判断を載せない。** 引数は無く、呼ばれたら終了する。上限も分岐も無いので、
Shell 側に「どう終了するか」を決める余地が残らない。

**B1 / B2 の観点で見ても上限は変わらない。** Stage が乗っ取られたときにできるのは
**Lumi を落とすこと**（ユーザーが再起動すれば戻る、可逆な妨害）であって、
OS 特権の獲得でも、記憶の書き換えでも、外部への送信でもない。
`captureScreen` / `injectInput` / `launchProcess` を露出しない規則は**そのまま**である。

**保証しないこと**: Stage が繰り返し `quit` を呼ぶことは止められない。
これは「起動するたびに即終了する」嫌がらせが可能ということであり、**可用性は守られていない。**
守っているのは権限の上限だけである。

#### `openCredits` を Stage に露出してよい理由〔Phase 1〕

`stage` の操作メニューから、Core の生死に依存しない静的な `credits` ウィンドウを開くために要る。
引数はなく、既に開いていれば同じウィンドウを前面へ出すだけである。任意の URL やウィンドウ指定を
Stage から渡せないため、外部通信や任意ウィンドウ生成の能力にはならない。

Tauri 実装では `shell_credits_open` を**非同期コマンド**としてスレッドプールで実行する。
同期 IPC ハンドラから `WebviewWindowBuilder::build()` を呼ぶと、Windows のメインイベントループが
ウィンドウ生成の完了待ちと循環し、アプリ全体のイベント処理が停止するためである。

#### `openOllamaSite` を Stage に露出してよい理由〔Phase 1〕

初回セットアップから Ollama の公式配布元へ移動するために要る。
Stage から URL を受け取らず、Shell に固定した公式ダウンロードページだけを既定ブラウザで開く。
したがって、侵害された Stage が得るのは同じ公式ページを繰り返し開く能力までであり、
任意サイトへの誘導や任意プロセス引数の指定には広がらない。

#### `scaleWindow` に「大きさ」ではなく「倍率」を渡す理由〔Phase 0〕

Stage が「この大きさにしろ」と絶対値を渡せると、**画面より大きい / 1 ピクセルの
ウィンドウを要求できてしまう。** Stage は信頼されていない（B1 / B2）ので、
**上限・下限を決めるのは Shell 側**にする（`compute_scaled_size` は純粋関数）。

同じ理由で移動は `setPosition` ではなく `startDragging`。**座標を Stage に計算させない。**

---

## `spawnSidecar` — Python Core の起動

```typescript
interface SidecarSpec {
  binary: string           // 同梱された Python 実行体
  args: string[]
  env: Record<string, string>   // WS token をここで渡す
  killOnParentExit: true        // ゾンビを残さない
}
```

### 要件

| # | 要件 |
|---|---|
| 1 | **Shell 終了時に Core も確実に終了する**（ゾンビを残さない） |
| 2 | Core が異常終了したら検知して再起動する |
| 3 | WS token を環境変数で渡す（コマンドラインに載せない） |
| 4 | stdout / stderr を Shell 側でログに落とす |

**Phase 0 の検証項目**: Core 強制終了 → Shell が再起動する。Shell 終了 → Core も終了する。

---

## `os.*` の検証層（B3）

**Shell は無条件に従わない。** Core の指示内容にかかわらず適用する検証。

```typescript
interface OsCommandValidator {
  // 1. 認証
  verifyToken(token: string): boolean

  // 2. allowlist
  isAllowed(command: string): boolean

  // 3. schema
  validate(command: string, payload: unknown): ValidationResult

  // 4. ハードコード拒否（Invariant 8）
  isProtectedTarget(target: WindowHandle | Point): boolean
}
```

### 保護対象（ハードコード。設定で無効化できない）

- 権限プロンプトウィンドウ
- Lumi 自身のメインウィンドウ・設定ウィンドウ

`os.input.*` / `os.capture.*` がこれらを対象とする場合、**無条件に拒否してログに残す**。

### B3 が保証すること

> Core が侵害された場合、攻撃者は Core に付与されている OS capability を行使できる。
> B3 が保証するのは (a) allowlist 外の操作ができない (b) 保護対象への入力・キャプチャができない (c) 権限昇格が自己承認で行われない、の3点のみ。
>
> **被害の完全な防止ではなく、権限の上限固定である。**

詳細 → [../contracts/security-boundaries.md](../contracts/security-boundaries.md)

---

## 純粋関数として切り出すもの

**AIRI の `window-contract.ts` の作法を借用する。** ウィンドウ設定を純粋関数にすることで、Tauri / Electron に依存せずユニットテストできる。

```typescript
// テスト可能な純粋関数
function computeStageWindowOptions(cfg: StageConfig): WindowSpec
function computeOverlayIsolation(cfg: OverlayConfig): IsolationSpec
function decideClickThrough(cursor: Point, region: HitRegion): boolean
function decideFadeState(cursor: Point, region: HitRegion, prev: FadeState): FadeState
```

これらは `PlatformShell` の実装に依存しないため、Tauri / Electron のどちらでも同じテストが通る。

---

## AIRI から借りる運用知見

**知見の一覧は [../architecture/ui.md](../architecture/ui.md) が唯一の定義場所。** ここでは対応する API だけを示す。

| 知見 | 対応する API |
|---|---|
| 他アプリがフルスクリーンでも最前面を維持 | `setAlwaysOnTop` の実装で考慮 |
| 表示時にフォーカスを奪わない | `showInactive` |
| 非アクティブ時にレンダリングが止まらない | Tauri 実装で WebView の設定 |
| 画面共有時に映らない選択肢 | `setContentProtection` |
| 自分自身を操作対象から除外 | `isProtectedTarget` |
| ウィンドウ位置の永続化 | `getPosition` / `setPosition` + Core 側で保存 |

---

## Electron 実装への退避（もし必要になったら）

| PlatformShell API | Electron 実装 |
|---|---|
| `setClickThrough` | `setIgnoreMouseEvents(on, { forward: true })` |
| `onCursorMove` | クリックスルー中も mousemove が届くので、フックが不要 |
| `showInactive` | `showInactive()` |
| `setContentProtection` | `setContentProtection()` |
| `spawnSidecar` | `child_process.spawn` + `app.on('will-quit')` |
| `captureScreen` | `desktopCapturer` |
| `injectInput` | `uiohook-napi` 等の外部モジュール |

**Electron の方が API が揃っている。** それでも Tauri を選ぶのは、メモリ数十MB級という利点が、ローカル LLM を動かす本プロジェクトでは大きいため（→ [ADR-001](../decisions/ADR-001-desktop-shell-tauri.md)）。

---

## テスト

| # | テスト |
|---|---|
| 1 | **純粋関数のユニットテスト**（`computeStageWindowOptions`, `decideClickThrough`, `decideFadeState`） |
| 2 | Shell 終了時に Core も終了する（ゾンビなし） |
| 3 | Core 強制終了時に Shell が検知して再起動する |
| 4 | WS token が無効な接続を拒否する |
| 5 | allowlist 外の `os.*` コマンドが拒否され、ログに残る |
| 6 | schema 違反の payload が拒否される |
| 7 | **保護対象ウィンドウへの `os.input.*` が拒否される**（Invariant 8） |
| 8 | **Core 側の BindVerifier を無効化しても Shell 側で拒否される**（二重化の確認） |
| 9 | カーソル監視の CPU 使用率が許容範囲（Phase 0 実測） |
| 10 | ホバー検知が hit_region の変化に追従する |
| 11 | **`compute_scaled_size` が上限・下限でクランプする**（Stage が画面外の大きさを要求できない） |
| 12 | **Core のサイドカーを起動してもコンソールウィンドウが出ない**（Windows） |
| 13 | **`compute_stage_placement` が作業領域の右下に収まる位置を返す**（マルチモニタのオフセットを含む） |
| 14 | **拡大しても右下の角が動かない**（`anchor_bottom_right`） |
| 15 | **開発ビルドではサイドカーよりソースを優先する**（固めた実行体が古いまま動かない） |
