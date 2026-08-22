# Phase 2 実測値

> **このファイルは設計ではなく観測の記録である。** 数値は環境に依存するので、
> **測った環境を必ず一緒に書く**（[phase0.md](phase0.md) / [phase1.md](phase1.md) と同じ規則）。

関連: [../roadmap.md](../roadmap.md) Phase 2, [../contracts/privacy.md](../contracts/privacy.md), [../decisions/ADR-040-encrypted-sqlite-driver.md](../decisions/ADR-040-encrypted-sqlite-driver.md)

---

## 測定環境

| | |
|---|---|
| OS | Windows 11 Home 26200 |
| Python | 3.12.11（uv で解決） |
| 測定日 | 2026-08-22 |

---

## spike: 暗号化 SQLite × sqlite-vec × FTS5 × PyInstaller〔2026-08-22〕

**Phase 2 の他の全部の前提。** [privacy.md](../contracts/privacy.md) 末尾の「未決」4項目に答えるために回した。
決定は [ADR-040](../decisions/ADR-040-encrypted-sqlite-driver.md)。

### 使ったもの

| | |
|---|---|
| ライブラリ | `apsw-sqlite3mc` 3.53.4.0 |
| SQLite | 3.53.4（SQLite3 Multiple Ciphers 2.4.0） |
| 暗号 | `chacha20`（sqleet 方式。**明示的に固定**） |
| wheel | cp312 win_amd64 / **3.48 MiB**（Windows wheel は**存在する**） |

### 結果

| # | 確認したこと | 結果 |
|---|---|---|
| 1 | 暗号化 DB に sqlite-vec をロードし、KNN が返るか | **返る**（`vec0` 仮想テーブル、distance 0.0） |
| 2 | 暗号化 DB で FTS5 が日本語を引けるか | **引ける** |
| 3 | ディスク上のファイルが平文でないか | **平文でない**（先頭 16 B はランダム。挿入した目印文字列はファイル内に現れない） |
| 4 | 標準 `sqlite3` で開けてしまわないか | **開けない**（`file is not a database`） |
| 5 | 誤った鍵で開けないか | **開けない**（`apsw.NotADBError`） |
| 6 | WAL が使えるか | **使える** |
| 7 | 平文 DB からの移行経路があるか | `ATTACH ... KEY ''` + `INSERT ... SELECT` で**可能**。`sqlite3mc_export` / `sqlcipher_export` 関数は**この配布物には無い** |
| 8 | PyInstaller の配布物に載るか | **載る**（`apsw/__init__.cp312-win_amd64.pyd` 2.73 MiB） |
| 9 | 固めた配布物で `--self-check` が通るか | **通る**（下記） |

### 開く時間（KDF 込み）

| 回 | `open` + `PRAGMA key` + `SELECT count(*)` |
|---|---|
| 1 回目 | 29.1 ms |
| 2 回目 | 21.8 ms |
| 3 回目 | 21.6 ms |

**起動時に1回。** 会話のクリティカルパス（[audio.md](../architecture/audio.md) §7）には乗らない。

### 配布物サイズ（R1）

同一コミットで、依存を足す前と後をそれぞれ `--clean` ビルドして比較した。

| | PyInstaller onedir |
|---|---|
| 追加前 | 271,878,003 B |
| 追加後 | 274,745,418 B |
| **増分** | **+2,867,415 B（+2.73 MiB / +1.05%）** |

**R1 は再燃しない。** 懸念は「torch 依存でインストーラ 1〜2 GB」であり、桁が3つ違う。

### 固めた配布物での `--self-check`

```text
✓ OS secret store: DPAPI (current user) round-trips the key
✓ Encrypted SQLite: chacha20 / APSW 3.53.4.0 / SQLite 3.53.4
✓ sqlite-vec: v0.1.9 / KNN search works in an encrypted DB
✓ FTS5: Japanese search matches in an encrypted DB
```

**dev 環境で通ることは、固めた配布物で通ることを意味しない。**
実際この2項目は、dev では緑で、サイドカーでは赤だった——
拒否された `sqlite3` 接続を閉じ忘れており、Windows では一時ディレクトリを消せずに落ちていた。
**そのために `--self-check` がある。**

### 判定

**[ADR-038](../decisions/ADR-038-privacy-and-data-retention.md) を修正する必要は無い。** 方針どおりに実装できる。

---

## 投機 STT〔2026-08-22 実装。**実測は未取得**〕

Phase 2b で [ADR-039](../decisions/ADR-039-speculative-stt.md) を実装した。**ここに載せる数値はまだ無い。**

| 取るべき値 | 状態 |
|---|---|
| 投機の破棄率（`stt.speculation_discarded` / `stt.speculation_started`） | **未取得** |
| `stt_overlap_ms` の分布（どれだけ実際に隠れたか） | **未取得** |
| `stt.speculation_capped` の発生率 | **未取得** |
| `critical_path_ms` の p50 / p95 | **未取得** |

**実装では埋まらない種類の数値である。** 破棄率は「人が文中でどれくらい間を置くか」で決まり、
`stt_overlap_ms` は STT が GPU か CPU かで決まる。**どちらもテストからは出てこない。**

予算上のクリティカルパス 1.10 s は**予算であって実測値ではない**
（[ADR-039](../decisions/ADR-039-speculative-stt.md) / [../architecture/audio.md](../architecture/audio.md) §7）。
実際に喋って `turn_latency` を集めるまで、**1.10 s を達成値として書かない。**

### 実装で確定したこと（数値ではない）

| | |
|---|---|
| 同時に走る STT | **常にたかだか1つ**（single-flight。世代を N 回進めても増えない） |
| 上限に達したターン | 投機を止め、終端確定後に1回だけ実行する（`stt_speculative: false` / `stt_overlap_ms: 0`） |
| 採用の判定 | 世代の一致のみ。**速さでは決めない**（不一致・失敗・世代不明はすべて確定バッファで再実行） |
| `unaccounted_ms` の基準 | `total_ms - critical_path_ms` に変更（`measured_sum_ms` ではない） |


---

## 埋め込みと記憶検索〔2026-08-22 / Phase 2e〕

決定は [ADR-041](../decisions/ADR-041-embedding-model-harrier-oss.md)。
測ったマシン: このリポジトリの開発機（CPU 4 threads / ONNX Runtime 1.28 / CPUExecutionProvider）。

### 量子化の比較（`onnx-community/harrier-oss-v1-270m-ONNX`）

| variant | ディスク | ms/query | fp32 との top1 一致 | 平均 \|Δcos\| |
|---|---:|---:|---|---:|
| fp32 | 1383 MiB | 50 | — | — |
| int8（`model_quantized`） | 328 MiB | **187** | 100% | 0.0004 |
| **q4（採用）** | **196 MiB** | **52** | 100% | 0.0104 |
| q4f16 | 164 MiB | 59 | 100% | 0.0104 |

> **int8 がいちばん遅い。** ONNX Runtime の CPU provider で、この decoder の int8 経路は
> fp32 の 3.6 倍かかった。**「量子化すれば速い」はここでは成り立たない。**
> 順位は4種とも一致したので、**速さとサイズの両方で妥協が無い q4** を採った。

### 指示文と文書形式（fp32 / 日本語10件 × 10クエリ）

| task | 文書 | top1 | recall@3 |
|---|---|---:|---:|
| memory 用（英語） | **`subject: content`** | **90%** | **100%** |
| memory 用（英語） | 本文のみ | 80% | 90% |
| memory 用（日本語） | 本文のみ | 90% | 90% |
| `web_search_query`（モデル同梱） | 本文のみ | 90% | 90% |
| 指示文なし | 本文のみ | 90% | 90% |

**10件 × 10クエリは smoke であってベンチマークではない。** この表を「日本語検索品質の評価」と読まない。

### 実装で確定した性質（数値ではないが、間違えると静かに壊れる）

| | |
|---|---|
| 出力 | `sentence_embedding` **[batch, 640]。プール済み・L2 正規化済み**（実測ノルム 1.0000） |
| 入力 | `input_ids` / `attention_mask` のみ |
| **パディング** | **右詰めのみ正しい。** 左詰めにすると同じ文が別ベクトルになる（**実測 cos 0.22**）。例外は出ない |
| トークナイザ | `<bos>` / `<eos>` を自動で付ける。**last-token pooling が読むのは `<eos>`** |
| バッチ | **ほぼ無料。** 1件 130 ms に対し 8件 138 ms（コストは呼び出し単位） |

### 取得と検索のコスト（q4 / 実測）

| | 実測 | 備考 |
|---|---:|---|
| モデル取得（196 MiB、pin 済み SHA-256 検証込み） | **49 s** | 初回のみ |
| Provider の `load()`（ONNX セッション + tokenizer） | **873 ms** | 起動時。会話は待たない（バックグラウンド Job） |
| 記憶 11 件のインデックス作成 | **100 ms** | 埋め込み + vec0 + FTS5 |
| **検索1回（クエリ埋め込み + vec + FTS + recent）** | **21〜23 ms**（うち埋め込み 22 ms） | **予算 0.05 s に収まる**（[../architecture/audio.md](../architecture/audio.md) §7） |

日本語11件での top1 は4クエリすべて期待どおり（猫 / 朝が弱い / AivisSpeech / 確定申告）。
**`AivisSpeech` は keyword + vector の両方が拾った**——固有名詞は FTS 側が効く例である。

### FTS5 trigram の限界（設計判断ではなく、事実）

| クエリ | 結果 |
|---|---|
| `猫`（1文字） | **ヒットしない** |
| `ミケ`（2文字） | **ヒットしない** |
| `飼って` / `確定申告` / `Factorio` | ヒットする |

**3文字未満は原理的に当たらない。** だからキーワード検索は補助であり、主役はベクトル側である
（`lumi/memory/vectors.py` は3文字未満の語をそもそも送らない）。
