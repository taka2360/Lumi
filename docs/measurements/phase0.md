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

取得されたものの実体〔2026-08-15〕:

| | |
|---|---|
| 保存先 | `%APPDATA%\AivisSpeech-Engine\`（Models / BertModelCaches / Logs。**`%LOCALAPPDATA%\Lumi\` の外**） |
| 既定モデル | 「まお」「山灘」の2つ。**どちらも ACML 1.0** |
| ライセンス全文の所在 | **モデルの manifest（`.aivmx`）の中**。話者名・制作者名も同じ場所 |

**「使用中のモデルのライセンス全文」はハードコードせずにここから読める。**
→ [../licensing.md](../licensing.md) §4.4（Phase 1 の `Provider.attribution()` の入力になる）

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

## 発話とリップシンク（検証手順 6）

「こんにちは」を実際に喋らせた〔2026-08-15〕。

| 区間 | 時間 |
|---|---|
| エンジン起動 → 応答（**2回目以降**） | **18.0 秒** |
| エンジン起動 → 応答（**初回**。エンジン自身のモデル取得を含む） | 約 2 分 |
| 合成（`audio_query` + `synthesis`。**初回はモデルのロードを含む**） | 3.9 秒 |
| 音声 | 1.19 秒（44100Hz mono） |

**この 3.9 秒は Phase 1 の SLO（第1文 TTS 生成 p50 0.20 秒）を大きく超える。**
初回のモデルロードであり2回目以降は縮むと見込まれるが、**未計測**。
Phase 1 で「起動時にウォームアップしておく」必要があるかを、ここで測り直して決める。

### エンジンのメモリ

| プロセス | Working Set |
|---|---|
| **AivisSpeech Engine（`run.exe`）** | **1.3 GB** |
| `lumi-shell.exe` | 39 MB |
| WebView2（子プロセス合計） | 約 300 MB |

**TTS が VRAM を使わない代わりに、RAM を 1.3GB 使う。**
Phase 5（Model Resource Manager）が扱うのは VRAM だけではない、という根拠になる。

### ★ 音素の長さが返ってこない

**AivisSpeech の `audio_query` は、モーラ長をすべて `0.0` で返す。**
`engine_manifest` の `adjust_phoneme_length: false` がその宣言（長さは合成時にモデルが決める）。

最初の実装は「`audio_query` の長さを積み上げる」だったため、
**口のタイムラインが 200ms（前後の無音の合計）になり、1.19 秒の音声に対して口がほぼ動かなかった。**

対処: **口の形はモーラ列から、時間は合成された音声の長さから**割り振る。
両方無ければビセームを送らない（でたらめな時間で口を動かさない）。
→ [../interfaces/renderer.md](../interfaces/renderer.md)「生成方式」

| | |
|---|---|
| 音声の長さ | 1187 ms |
| タイムラインの長さ | **1187 ms**（一致） |
| span 数 | 5（モーラ数と一致） |

### エンジンプロセスの生存（検証手順 8 の続き）

**Shell を強制終了したら、エンジンも一緒に落ちた**〔2026-08-15 実測〕。

Core が起動した `run.exe` が、Shell の Job Object（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）を
継承しているため。**エンジン用のゾンビ対策を別に持つ必要はない**（Windows では）。
→ [../architecture/core.md](../architecture/core.md) §6「所有と生存」

## クレジット画面（検証手順 18）

| 項目 | 結果 |
|---|---|
| 5つの節（Lumi / 同梱 OSS / 同梱しないもの / 音声のクレジット / 禁止事項）が出る | **✓** |
| ライセンス全文（MIT / LGPL-3.0 / GPL-3.0 / ACML 1.0）が省略なしで読める | **✓** 目視 + テスト |
| **Core に接続していない** | **✓** `credits` は別バンドル（64.9KB / three.js も WS も含まない）。テストで import を静的に禁止 |
| トレイ → クレジットで開く | **✓**〔2026-08-15 目視確認〕 |

ライセンス全文は**加工せずそのまま**出す（Markdown 記法も原文のまま）。読みやすさのために
整形すると、原文と違うものを提示することになる。

## 音声デバイスとストリーム（検証手順 12）

**判定: duplex stream は使わない。入出力を別ストリームで開く。** → [ADR-020](../decisions/ADR-020-split-audio-streams.md)

再現方法: `cd core && uv run python -m lumi.audio.probe --seconds 120`
（無音しか書かないので音は出ない。マイクの音は記録も送信もしない）

### 測定に使った機材

| 役割 | 機材 |
|---|---|
| 入力 | Px7 S3 USB ヘッドセットのマイク（WASAPI 48 kHz） |
| 出力 A | 同じヘッドセットのヘッドホン（WASAPI **96 kHz**） |
| 出力 B | PL3270Q（NVIDIA HDMI, WASAPI 48 kHz） |
| 出力 C | オンボード Realtek（WASAPI 48 kHz） |

blocksize 512 / int16 / mono。

### duplex の可否

| 組み合わせ | 結果 |
|---|---|
| WASAPI / **別物理デバイス**（USB マイク 48k + HDMI 48k） | **開ける。** xrun 0、遅延 in 22 ms / out 33 ms |
| WASAPI / **同一物理デバイス**（USB ヘッドセット: マイク 48k + ヘッドホン 96k） | **開けない**（`Invalid sample rate`） |
| WASAPI / 16 kHz（VAD のレート） | **開けない**。共有モードは mix format のレートのみ受け付ける |
| ホスト API を跨ぐ（MME 入力 + WASAPI 出力） | **開けない**（`Illegal combination of I/O devices`。PortAudio が構造的に拒否） |
| MME / 同一・別物理とも | 開ける。ただし**出力遅延 209 ms**、xrun あり |

**「同一デバイスなら開ける」ではない。** 条件は「同一ホスト API かつ両者が受け入れる単一のレートが存在すること」。
同一の USB ヘッドセットですらマイクとヘッドホンで mix format が違い、**開けなかった**。

### クロックドリフト（分離ストリーム）

| 組み合わせ | 測定時間 | 相対ドリフト |
|---|---|---|
| USB マイク vs HDMI モニタ | 120 s | -1.5 ppm |
| USB マイク vs オンボード Realtek | 90 s | +1.8 ppm |
| USB マイク 48k vs 同じヘッドセットの出力 96k | 120 s | +1.4 ppm（`lumi.audio.probe` の出力。xrun 0、遅延 22 ms） |

**★ この数値は測定分解能とほぼ同じである。** コールバックの先読み量が 3 ms 程度揺れるため、
120 秒の測定で分離できるのは**数 ppm まで**。正しい主張は「1.5 ppm だった」ではなく
**「分解能（数 ppm）以下で、有意なドリフトは観測されなかった」**。

duplex（単一コールバック）の +3.4 ppm と、split の入力 +4.1 / 出力 +5.6 ppm にも**有意差は無い**。
つまり **duplex にしても得るものが無い。**

### 保証しないこと

- **測ったのはアプリから見たストリームのペース**であり、スピーカーから出てマイクに入るまでの実遅延ではない
- WASAPI **排他**モード・Windows 以外の OS は未測定
- ドリフトが小さいのは **Windows 共有モードのサンプルレート変換がデバイス間の差を吸収した結果である可能性が高い。**
  他 OS では成り立たない。Phase 2 の AEC は遅延推定を必ず持つ

### ★ 開けることと、聞こえることは別

| 事象 | 実測 |
|---|---|
| 録音デバイスが1つも無い PC | **このマシンの初期状態がこれだった。** MME / DirectSound / WASAPI のどれも入力デバイスを1つも報告しない |
| `open()` は成功するのにフレームが1つも来ない | Realtek のマイク端子（WDM-KS のカーネルピンとしては開ける）。**3秒待ってもコールバックが1度も呼ばれない** |
| 電源の入っていないモニタ / 接続されていない Bluetooth ヘッドセット | デバイス一覧には出る。**先頭に出る**こともある |

**したがって「最初のフレームが規定時間内に届くこと」を開通の条件にする。**
デバイスは**ユーザーが OS で選んだ既定**を使う（ホスト API ごとの既定。`sd.default.device` は
PortAudio が選んだホスト API のものなので見ない）。

### ★ WASAPI ストリームをワーカースレッドで開くと落ちる

`Unanticipated host error [PaErrorCode -9999]` になる。**WASAPI は COM を使い、COM の初期化はスレッドごと**に要るため。
生成・開始・停止は同じスレッドで行う。測定はコールバック（PortAudio 側のスレッド）で進むので、
1つのスレッドから2本開けば入出力は同時に走る。

---

## sqlite-vec / FTS5（検証手順 13）

| 環境 | 結果 |
|---|---|
| uv 管理の Python 3.12.11（SQLite 3.49.1） | **✓** `vec_version = v0.1.9`、`vec0` 仮想テーブルの KNN 検索が動く。FTS5 も利用可 |
| **同梱サイドカー（PyInstaller onedir）** | **✓**〔2026-08-15。`lumi-core.exe --self-check`〕 |

CI で回し続けるテスト: `core/tests/test_sqlite_extensions.py`

---

## パッケージング（未確定事項 #2 / 検証手順 1・13）

**判定: PyInstaller の onedir。** → [ADR-021](../decisions/ADR-021-sidecar-packaging.md)

| | onefile | **onedir（採用）** |
|---|---|---|
| Core のサイズ | 13.4 MB（1 ファイル） | 26.7 MB（89 ファイル） |
| 起動（`--self-check` の実時間） | 803, 787, 808, 775, 949 ms | 919（初回）, 262, 263, 263, 265 ms |
| **強制終了したとき** | **`%TEMP%` に 21.4 MB が残る** | **何も残らない** |

参考: 開発経路（`uv run lumi-core`）は 344〜609 ms。

### ★ 固めた実行体だけで壊れるもの

| 事象 | 原因 |
|---|---|
| **日本語ログを書いた瞬間に Core が落ちる** | 固めた実行体は stdout が **cp932** になる。Core のログは日本語を含み、**Shell はそれを stdout から読む契約**。`uv run` では UTF-8 なので開発中は気づけない → `lumi/__main__.py` で UTF-8 に固定した |
| **ASIO 版の PortAudio が混入する** | `sounddevice` のフックが `_sounddevice_data` を丸ごと集める。**指定しなくても入る。** Steinberg の SDK は非 OSS → spec で除外し、**残っていたらビルドを失敗させる** |

### ★ 同梱したら、開発時もそれが動いてしまった

`bundle.resources` を足すと、**`tauri dev` も `target/debug/core/` に配る。**
サイドカーを優先していたため、**Python を編集しても反映されず、前回固めた実行体が動いた**
（2026-08-15。新しい状態フィールドがログに出ないことで気づいた）。

**開発ビルドではソース（`uv run`）を優先する。** 配布物には `core_project_dir` が
無いので、常にサイドカーが選ばれる（`resolve_launch_spec`）。

### ★ コンソールウィンドウが出る

Core は console サブシステムの実行体（**stdout の構造化ログを Shell が読む契約**なので
windowed にはできない）。そのまま起動すると黒い窓が並んで出る。しかも
**ユーザーがその窓を閉じると Core が死に、Supervisor が正しく再起動して、また挨拶する。**

対処は `CREATE_NO_WINDOW`（Shell 側の spawn に付ける）。エンジン起動時にも同じ扱いをしている。
確認方法: `ConsoleWindowClass` のウィンドウが存在しないこと。

### 検証手順 13 — サイドカーからの sqlite-vec

**✓ 固めた実行体で確認済み。** `lumi-core.exe --self-check`:

```
✓ sqlite-vec: v0.1.9 / KNN 検索が動く
✓ FTS5: SQLite 3.49.1
✓ PortAudio: 入力 15 / 出力 34 / ホスト API: MME, DirectSound, WASAPI, WDM-KS
✓ TLS: CA 証明書 72 件
```

---

## R1 — インストーラサイズ / RAM / VRAM

**判定: R1 は解消。** torch を避けた結果、Python Core を同梱して **13.1 MB**。

| 項目 | 値 |
|---|---|
| **インストーラ（NSIS）** | **13.10 MB** |
| `lumi-shell.exe` | 8.59 MB |
| `core/`（Python Core 一式） | 26.75 MB / 89 ファイル |
| Shell の RAM（アイドル） | 37 MB |
| Core の RAM（アイドル） | 50 MB |
| WebView2（6 プロセス合計） | 393 MB |
| **Lumi 本体の合計（TTS エンジンを除く）** | **497 MB** |
| TTS エンジン（別プロセス） | 1,284 MB |
| **VRAM（Lumi 起動前後の差分）** | **55 MiB** |

VRAM は WebView2 の合成と three.js のプレースホルダ描画のみ。**LLM を積む前の値。**
per-process の GPU カウンタは Windows では取得が重く実用にならなかったため、**起動前後の差分**で測っている。

### ★ インストーラに 33 MB のモデルが入っていた

最初のビルドは **39.14 MB** で、そのほとんどが `stage/public/` に置いていた
開発用 VRM（33 MB）だった。**Vite は `public/` を配布物へ丸ごとコピーする。**

| | サイズ |
|---|---|
| モデルとソースマップが入っていたとき | 39.14 MB |
| 除いた後 | **13.10 MB** |

同梱してよいモデルは未決（[../licensing.md](../licensing.md) §7 未確認 #5）なので、
**入っていたこと自体がライセンス上の危険**だった。対処:

- 開発用の実体は `stage/dev-assets/`（**`public/` ではない**）に置く
- dev サーバだけが `/character.vrm` として配る（`apply: "serve"` のプラグイン）。
  **本番ビルドにはこの経路が存在しない**ので、構造的に混入しない
- ソースマップを本番ビルドで無効化（5 MB）

### プロセスの後始末（検証手順 8・パッケージ版）

`lumi-shell.exe` を `Stop-Process -Force` → **Core も TTS エンジンも消えた。** ゾンビなし。

---

## 別マシンでの検証（Phase 0 の完了条件）〔2026-08-16〕

**判定: 完了条件を達成した。** インストーラから導入し、キャラクターが立って一言喋るところまで、
**ビルドしたマシン以外で**通った。

| | |
|---|---|
| 確認したこと | インストーラで導入 → 起動 → キャラクターが立つ → 一言喋る |
| **確認していないこと** | **検証手順 15〜18**（「取得しない」を選んだときの起動 / ネットワーク遮断時の失敗とロールバック / 選択前に外部通信が起きないこと / クレジット画面の内容） |
| 測定環境 | **記録していない。** 開発機（冒頭の表）とは別の Windows マシン、という以上のことが残っていない |
| 数値 | **無い。** これは「動くか」の確認であって測定ではなかった |

> **環境を記録していないのは、このファイルの規則（「測った環境を必ず一緒に書く」）に対する例外である。**
> 隠さずに書いておく。次に別マシンで回すときは環境を先に控える。

### 15〜18 を Phase 1 に持ち越す理由

**4項目とも「取得の経路」に関するものであり、Phase 1 で同じ経路に LLM / STT のセットアップが乗る**
（[ADR-023](../decisions/ADR-023-llm-runtime-and-model-acquisition.md)）。
経路が増えたあとにまとめて回す方が、通ったときの意味が強い。

開発機では 15 / 17 / 18 を確認済み（上記の各節）。**16（実ネットワークでの断線）はどちらのマシンでも未実施。**

---

## 未測定（Phase 0 中に埋める）

- [x] ~~入出力が別デバイスのときの duplex stream~~〔2026-08-15 完了 → 上記 / [ADR-020](../decisions/ADR-020-split-audio-streams.md)〕
- [x] ~~**同梱サイドカー**での sqlite-vec のロード~~〔2026-08-15 完了〕
- [x] ~~インストーラサイズ・アイドル時 RAM / VRAM~~〔2026-08-15 完了〕
- [x] ~~Python サイドカーのパッケージング方式の比較~~〔2026-08-15 完了 → [ADR-021](../decisions/ADR-021-sidecar-packaging.md)〕
- [ ] release ビルドでのカーソル監視 CPU
- [x] ~~**別マシンでのインストールと起動**~~〔2026-08-16 完了 → 上記「別マシンでの検証」。**検証手順 15〜18 は持ち越し**〕
