# ADR-001: Desktop Shell に Tauri 2 を採用し `PlatformShell` で抽象化する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../interfaces/shell.md](../interfaces/shell.md), [../architecture/ui.md](../architecture/ui.md) |

---

## Decision

デスクトップシェルに **Tauri 2（Rust）** を採用する。
同時に **`PlatformShell` インターフェース**を定義し、Electron への退避路を確保する。

Stage（WebView 内の React + TypeScript）は抽象化しない。Tauri でも Electron でもそのまま動くため。

---

## Reason

### メモリが最大の理由

本プロジェクトはローカル LLM（6.5GB VRAM）、STT、Embedding、TTS を同時に動かす。**RAM と VRAM をできる限り AI に回したい。**

| | Tauri 2 | Electron |
|---|---|---|
| ベース RAM | 数十MB | 200〜400MB |
| バイナリサイズ | 数MB〜 | 100MB〜 |

数百MBの差は、通常のアプリでは誤差だが、**ローカル LLM を動かすマシンでは意味を持つ**。

### 独立性

AIRI は Electron を採用している。Tauri を選ぶことで、実装の借用が構造的に不可能になり、「参考にするが依存しない」という方針が担保される。

### Rust による OS 特権の実装

Shell は screenshot / input injection / window 操作を持つ。**Rust の型システムと所有権が、この危険な領域の実装品質に寄与する。**

---

## Alternatives

### A. Electron

**利点:**
- `setIgnoreMouseEvents(true, { forward: true })` が**そのまま要件を満たす**
- `showInactive()`, `setContentProtection()` など、デスクトップペット固有の API が揃っている
- AIRI の実装ノウハウがそのまま参考になる
- Node 生態系（サイドカー管理、ファイル操作）が使いやすい

**欠点:**
- ベース RAM 200〜400MB
- **Extension が Node フルアクセスのプロセスに同居するリスク**（AIRI の実際の問題）

### B. Rust ネイティブ GUI（egui / iced）

**利点:** 最軽量
**欠点:** VRM 描画（three.js）と Widget（iframe sandbox）が使えない。WebGL/WebView の資産を全部捨てることになる

### C. WebView2 直接利用（Rust + windows-rs）

**利点:** Tauri より薄い
**欠点:** Tauri が提供するウィンドウ管理・IPC・ビルドパイプラインを全部自作することになる

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| **クリックスルー + ホバー検知の自前実装** | `setIgnoreCursorEvents` に `forward: true` 相当が無い。Rust 側で Win32 カーソル監視が必要（推定 100〜200行） |
| Windows 透過の未検証 | 非フォーカス表示 + 常時最前面 + 透過の同時成立を Phase 0 で確認する |
| Python サイドカーのパッケージング | `externalBin` で可能だが、サイズが未知（R1） |
| 抽象化レイヤーのコスト | `PlatformShell` の定義と Tauri 実装の分離 |

### 得るもの

- RAM 数百MBをローカル AI に回せる
- AIRI からの構造的独立
- Shell の実装が Rust の型システムに守られる

---

## Consequences

### Phase 0 が判定ポイントになる

以下が Phase 0 の完了条件に含まれる。

1. 透過ウィンドウが常時最前面で表示される
2. **背後のウィンドウが操作できる**（クリックスルー）
3. **キャラクターの上でホバーが検知される**（R2 判定）
4. インストーラサイズの実測（R1 判定）
5. カーソル監視の CPU 使用率が許容範囲

**破綻したら `PlatformShell` の Electron 実装に切り替える。** Stage は変更不要。

### 純粋関数として切り出す

AIRI の `window-contract.ts` の作法を借用し、ウィンドウ設定を純粋関数にする。

```typescript
computeStageWindowOptions(cfg): WindowSpec
decideClickThrough(cursor, region): boolean
decideFadeState(cursor, region, prev): FadeState
```

**これらは Tauri / Electron のどちらでも同じテストが通る。** 抽象化の実効性がここで担保される。

### 抽象化の範囲を限定する

`PlatformShell` は Shell 側の API だけを抽象化する。Stage の React コードは抽象化の対象外。

**「将来使うかもしれないから」ではなく、「Phase 0 で失敗する可能性が実在するから」抽象化する。** 設計原則7（過剰な抽象化を避ける）に照らして正当化される。
