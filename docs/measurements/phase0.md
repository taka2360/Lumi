# Phase 0 実測値

> **このファイルは設計ではなく観測の記録である。** Phase 5（Model Resource Manager）と
> R1 / R2 / R4 の判定根拠になる。数値は環境に依存するので、**測った環境を必ず一緒に書く**。

関連: [../roadmap.md](../roadmap.md) Phase 0「検証手順」, [../DESIGN.md](../DESIGN.md) §7

---

## 測定環境

| | |
|---|---|
| OS | Windows 11 Home 26200 |
| Shell | Tauri 2.11.3 / WebView2 |
| Rust | 1.97.1 (x86_64-pc-windows-msvc) |
| Node / pnpm | v24.19.0 / 11.21.0 |
| Python | 3.12.11（uv 0.11.17 で解決） |
| ディスプレイ | 拡大率 100%（devicePixelRatio = 1） |

---

## R2 — 透過 + 常時最前面 + クリックスルー + ホバー検知

**判定: 成立する。** Tauri 2 で `PlatformShell` を Electron に差し替える必要は現時点で無い。

| # | 項目 | 結果 | 測り方 |
|---|---|---|---|
| 1 | 透過ウィンドウ + 枠なし + 非フォーカス表示 | ✓ | 起動して目視 〔要ユーザー確認〕 |
| 2 | クリックスルー（キャラクター外） | **✓** | `WindowFromPoint` が背後の別アプリのウィンドウを返す |
| 3 | クリックスルーの解除（キャラクター内） | **✓** | `WindowFromPoint` が Lumi の WebView 子ウィンドウ（root = Tauri Window）を返す |
| 4 | ホバー検知 | **✓** | カーソルを領域内外に移動 → `shell.hover.state Inside/Outside` がログに出る |

当たり判定領域は **Stage がキャラクターの描画結果（bounding box の8頂点を投影した矩形）から算出**して
Shell に渡している。プレースホルダでも VRM でも同じ経路。

計測ログ（2026-08-15、debug ビルド）:

```
window rect: 312,312 - 792,1032      # 480x720 物理ピクセル
中心 (240,360) → shell.hover.state Inside    WindowFromPoint root = Lumi
隅   (5,5)     → shell.hover.state Outside   WindowFromPoint root = 別アプリ
```

### カーソル監視のコスト

| | 値 |
|---|---|
| ポーリング間隔 | 16ms（約 60Hz） |
| `lumi-shell.exe` の CPU | **1コアの約 2.8%**（debug ビルド、10秒平均） |
| `lumi-shell.exe` の Working Set | 41.1 MB（WebView2 の子プロセスを含まない） |

**判定: 許容範囲。** ただし debug ビルドの値であり、release と WebView2 込みの実測は
インストーラ作成時（Step M）に取り直す。悪化した場合の代替は
ローレベルマウスフック（`SetWindowsHookEx`）への切り替え（[../interfaces/shell.md](../interfaces/shell.md)）。

---

## プロセス管理（検証手順 7・8）

| # | 項目 | 結果 |
|---|---|---|
| 7 | Core を強制終了 → Shell が検知して再起動 | **✓** WS エラー検知 → `core.exited` → バックオフ後に再起動 → 新しいポートで再接続 |
| 8 | Shell を強制終了 → Core も終了（ゾンビなし） | **✓（ただし対策を追加した。下記）** |

### ゾンビ問題 — 当初の設計では防げなかった

**最初の実装（明示的 kill ＋ Core 側の stdin EOF 監視）では、Shell を `Stop-Process -Force` した後に
Core 関連プロセスが3つ残った。**

原因は開発時のプロセス構成 `Shell → uv.exe → python.exe`。

- 明示的 kill は**強制終了では走らない**
- stdin EOF による自己終了は**単体では動く**（実測で確認済み）が、間に `uv.exe` が挟まると漏れた

対策として **Windows Job Object（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）** を追加した。
Shell のプロセスハンドルが OS に閉じられた時点で、ジョブ内のプロセスがまとめて終了する。

再測定: 強制終了前 3 プロセス → 強制終了後 **0 プロセス**。

> **他 OS では別の手段が要る**（プロセスグループ / `PDEATHSIG`）。Phase 0 の対象は Windows。

---

## B3 の骨格（検証手順 9）

Core → Shell の `os.*` を、実際に走っている両プロセス間で確認した（`LUMI_DEV_OS_PROBE=1`）。

| 送ったもの | 結果 | ログ |
|---|---|---|
| `os.window.get_position`（allowlist 内） | 実行され `{x:130, y:130}` を返す | `os.command.executed` |
| `os.window.destroy`（**未知**） | **拒否** `unknown_method` | `os.command.rejected`（WARN） |
| `os.input.click`（**Phase 4c まで未実装の lane**） | **拒否** `not_implemented` | `os.command.rejected`（WARN） |

**`os.input.*` は「まだ無い」ではなく「明示的に拒否する」**。Invariant 8 の穴の決着（Phase 4c）前に
経路が開いてしまわないようにするため。

---

## sqlite-vec / FTS5（検証手順 13）

| 環境 | 結果 |
|---|---|
| uv 管理の Python 3.12.11（SQLite 3.49.1） | **✓** `vec_version = v0.1.9`、`vec0` 仮想テーブルの KNN 検索が動く。FTS5 も利用可 |
| **同梱サイドカー（PyInstaller）** | **未確認 → Step M** |

CI で回し続けるテスト: `core/tests/test_sqlite_extensions.py`

---

## R1 — インストーラサイズ / RAM

〔Step M で測る〕

| 項目 | 値 |
|---|---|
| インストーラ（NSIS） | — |
| Shell の RAM（アイドル） | — |
| Core の RAM（アイドル） | — |
| VRAM（アイドル） | — |

---

## 未測定（Phase 0 中に埋める）

- [ ] 入出力が別デバイスのときの duplex stream（未確定事項 #4 / Step K）
- [ ] **同梱サイドカー**での sqlite-vec のロード（Step M）
- [ ] release ビルドでのカーソル監視 CPU
- [ ] インストーラサイズ・アイドル時 RAM / VRAM（Step M）
- [ ] Python サイドカーのパッケージング方式の比較（PyInstaller vs uv 同梱。未確定事項 #2 / Step M）
