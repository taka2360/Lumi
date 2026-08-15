---
paths:
  - "shell/**/*.rs"
  - "shell/**/*.toml"
  - "shell/**/*.json"
---

# Lumi Shell — Tauri 2 / Rust

設計 → [architecture/ui.md](../../docs/architecture/ui.md), [interfaces/shell.md](../../docs/interfaces/shell.md), [contracts/security-boundaries.md](../../docs/contracts/security-boundaries.md) の B3, [ADR-015](../../docs/decisions/ADR-015-core-shell-boundary.md)

## Shell の責務

**OS 特権プリミティブのみ。判断を持たない**（Invariant 8 の拒否を除く）。

ウィンドウ（透過 / 最前面 / クリックスルー / ヒットテスト） / ホットキー / カーソル監視 /
スクリーンキャプチャ / 入力インジェクション / Core サイドカーの起動・監視・終了 / トレイ / `os.*` の検証。

## Shell は Core を信頼しない（B3）

**Core が侵害されただけで OS 乗っ取りになってはならない。**
`os.*` に対して、**Core の指示内容にかかわらず**次を適用する。

1. WS token による認証
2. `os.*` コマンドの allowlist（未知のコマンドは拒否してログ）
3. schema validation
4. **保護対象ウィンドウへの `os.input.*` / `os.capture.*` の無条件拒否**（Invariant 8）

### 「判断を持たない」と「無条件に従う」は違う

| | Shell が持つか |
|---|---|
| 「この操作をすべきか」の判断 | **持たない**（Core が決める） |
| 「この要求は形式的に妥当か」の検証 | **持つ** |
| 「この対象は絶対に触ってはいけない」の拒否 | **持つ** |

**Shell に置くのは「拒否」だけ。「許可」は絶対に置かない。**
拒否権は安全側にしか倒れないので権威の分散にならないが、許可を置いたら Invariant 1 の実質的な破壊になる。

### 保証しないことを書く

**「Core 侵害でも OS 乗っ取りにならない」とは書かない。** B3 が保証するのは権限の**上限固定**であって被害の防止ではない。
誇張した保証を書くと、実装者がそれを前提に他の防御を省く。

## `shell.*` には AI の判断を運ばない

- `shell/` から `Activity` / `Drive` / `MemoryRecord` などの AI 判断に関わる型を import しない
- `shell.*`（Tauri IPC）はウィンドウ自身の見た目と入力だけ。**1ms 以下であるべきもの**

## 純粋関数に切り出す

ウィンドウ設定・ヒットテスト・フェード判定は**純粋関数**にし、Tauri に依存せずユニットテストする。

```rust
fn compute_stage_window_options(cfg: &StageConfig) -> WindowSpec
fn decide_click_through(cursor: Point, region: &HitRegion) -> bool
```

## Tauri 2 固有の注意

- **`setIgnoreCursorEvents` に Electron の `forward: true` 相当が無い。** Rust 側でカーソル位置を監視して自前でクリックスルーを切り替える
- 他アプリがフルスクリーンでも最前面を維持する / 表示時にフォーカスを奪わない / 非アクティブ時にレンダリングを止めない
- **サイドカーは親の終了で確実に終了させる**（ゾンビを残さない）
- **WS token は環境変数で渡す。コマンドラインに載せない**

## Phase 4c 着手前

**Invariant 8 は HWND 判定だけでは守れない。**
全画面キャプチャにプロンプトが写る / `SendInput` は HWND ではなく座標に届く。
対処（`WDA_EXCLUDEFROMCAPTURE` の常時適用 / `WindowFromPoint` による注入直前判定 / プロンプト表示中の入力凍結）を
**決着させてから `os.input.*` の本実装をする。** → [contracts/invariants.md](../../docs/contracts/invariants.md) の Invariant 8
