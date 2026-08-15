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

## 初回セットアップ（検証手順 15・17）

実際に起動して、Core → Stage の経路で確認した〔2026-08-15〕。

| # | 項目 | 結果 |
|---|---|---|
| 15 | 「取得しない」を選んでも Lumi が起動し、「TTS 未セットアップ」が表示される | **✓** `setup.prompt.answered choice=skip` → 状態は `not_configured` のまま、パネルに明示される |
| 17 | ユーザーが選択するまで外部へのネットワークアクセスが発生しない | **✓（静的検査 + 経路）** `httpx` を使うのは `setup/install.py` だけ、それを呼ぶのは `setup/coordinator.py` だけ、というテストを CI に入れた |
| — | セットアップ UI の上でクリックスルーが解除される | **✓** `WindowFromPoint` がパネル位置で Lumi を返し、透過部分では背後のアプリを返す |
| 16 | ネットワーク断で失敗し、部分的な残骸が残らない | 単体テストでは確認済み（`.tmp-*` が残らない）。**実ネットワークでの断線試験は未実施** |
| 14 | インストーラに TTS エンジンのバイナリが含まれない | **✓（設計上）** 配布物には入れず実行時取得。インストーラ実物での確認は Step M |

### 実取得の結果〔2026-08-15〕

| 項目 | 値 |
|---|---|
| 取得物 | AivisSpeech Engine 1.2.0（Windows x64、`.7z.001`） |
| ダウンロード | 216.5 MB / **8.4 秒** |
| **SHA-256** | **ピン留めした値と一致**（`bfbceba2…71f3`）。ピン留めが正しいことの実証 |
| 展開（bsdtar） | 約 8 秒 |
| 展開後サイズ | **814 MB** |
| 実行体 | `…\Lumi\engines\aivisspeech-1.2.0\Windows-x64\run.exe` |
| 一時ディレクトリの残骸 | **なし** |
| 合計所要 | 16.8 秒 |

### ★ エンジンの初回起動が、さらに外部から取得する

**`run.exe` を初めて起動すると、エンジン自身が**次を取りに行く（Lumi の検証経路の外側）。

- 既定の音声モデル（AIVMX）を `api.aivis-project.com` から
- BERT モデル・トークナイザを HuggingFace から

起動完了までに**約 2 分**（回線による）。設計への反映 → [../architecture/setup.md](../architecture/setup.md) §4

### 合成の確認

```
POST /audio_query?speaker=888753760&text=こんにちは → accent_phrases 1 個 / モーラ 5 個
POST /synthesis                                   → 44100Hz mono 1.22 秒
```

**`audio_query` の `accent_phrases` にモーラ単位の情報が入る。**
これは**リップシンクの入力にそのまま使える**（振幅からの推定より正確）。
→ Step J で [../architecture/ui.md](../architecture/ui.md) のリップシンク方式（Provisional）を確定させる。

> 注意: `curl` で日本語を渡すと文字化けして `accent_phrases` が空になり、
> **無音に近い音声が返る**（エラーにならない）。テストのときに気づきにくい。

### 実測で見つかった競合

**Stage の接続が、Core の検出処理より先に来る。**

```
transport.connected role=stage   03:59:39.801
setup.state not_configured       03:59:40.396   ← 0.6 秒あとに確定
```

Stage が繋がった時点では状態が `unknown` であり、そのまま判定すると
**尋ねるべきときに尋ねずに終わる**（実際に prompt が出なかった）。
検出の完了を待ってから判定するよう修正し、再発防止のテストを入れた。

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
