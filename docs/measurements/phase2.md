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


---

## Reflection（記憶の抽出）〔2026-08-23 / Phase 2f〕

`qwen3.5:9b`（Ollama / Q4_K_M / think 無効 / temperature 0.2）に、日本語10行の会話を渡した。
**実装では出ない種類の数値であり、プロンプトの言い回しで壊れる。**

### 抽出プロンプトで実際に壊れた2点

| 症状 | 原因 | 直し方 |
|---|---|---|
| **常に `[]` を返す**（3/3） | 依頼が system プロンプトにしか無く、user メッセージが転写で終わっていた | **転写の後ろに問いを置く**（`TRANSCRIPT_CLOSING`）。→ 3/3 で正しく抽出 |
| **全部が同じ subject**（`episode.move` に6件） | 「subject は topic を指す」規則が弱かった | subject 規則を明記（下記）。→ 4件が4つの subject に分かれた |

**2つ目は静かな事故になる。** semantic な記憶が subject を共有すると `reconcile` は矛盾と見なし、
**無関係な事実が別の事実を supersede して消える**。プロンプトの1文が記憶の消失に直結する例である。

> 転写の後ろに問いを置くのは、**隔離の形としても正しい**——
> 信頼された指示が最後に読まれ、データが Lumi 自身の言葉で挟まれる
> （[../contracts/provenance.md](../contracts/provenance.md)）。

### 直した後の1回（同じ会話・4.4 s。空の DB なので `novelty` は全件 1.0）

| subject | content | mode | salience |
|---|---|---|---|
| `user.work` | ユーザーは今日一日 Rust を書いていた。 | user_stated | 0.45 |
| `episode.moving_soon` | 来月に引っ越す予定で、今の部屋では猫を飼えない。 | user_stated | 0.49 |
| **`user.pet_preference`** | 三毛猫を飼いたがっており、名前を「ミケ」と決めている。 | user_stated | **0.70** |
| `episode.mars_joke` | 冗談で自分は火星人だと述べた。 | user_stated | 0.29 |

- **「これ覚えておいて」と言われた記憶が最も高い**（0.70）。決定論的補正が効いている
- **「冗談だけど、僕は火星人」は事実として保存されなかった。**
  出来事として「冗談で述べた」と記録され、salience は最低（0.29）
- 抽出 1 回あたり **4.4 s**。会話のクリティカルパスには乗らない（アイドル時の Job）

> **この表は空の DB での値である。** `novelty` は既知の subject なら 0.0 になるので、
> **同じ会話を使い込んだ DB に対して流すと全件 0.15 低い。** 順位は変わらない。

### 残っている揺れ

**subject の名前も assertion_mode も実行ごとに揺れる**
（`user.pet_preference` / `user.pet_name_plan`、猫の件は `inferred` の回と `user_stated` の回がある）。
既存 subject をプロンプトに列挙して収束させる設計だが、**その効果はまだ測っていない。**

---

## 生成設定（sampling）の A/B〔2026-09-02 / ADR-048〕

`qwen3.5:9b`（Ollama 0.33.2 / Q4_K_M / think 無効 / GPU）。
harness → `core/scripts/llm_profile_eval.py`。
**5ケース × seed 2 系列（1000 / 2000）= 変種ごとに10応答。** 本番の `assemble()` と
Content Pack の persona をそのまま通しており、プロンプトは製品と同一である。

### まず分かったこと — Lumi は3つのパラメータを Modelfile に決めさせていた

`/api/show` が返す `qwen3.5:9b` の Modelfile：

```text
temperature 1 / top_k 20 / top_p 0.95 / presence_penalty 1.5
```

Lumi が送っていたのは `temperature` だけだった。**残りはこのファイルが決めていた。**

seed を固定して「省略」と「明示送信」の出力を比べると確認できる（同一なら既定と一致）。

| 送ったもの | 出力 | 結論 |
|---|---|---|
| 省略 / `presence_penalty=1.5` を明示 | **完全一致** | 1.5 が実際に効いている |
| `presence_penalty=0.0` | 変化する | 罰則は無視されていない |
| 省略 / `repeat_penalty=1.0` を明示 | **完全一致** | Ollama 0.33.2 の既定は 1.0（1.1 ではない） |
| 省略 / `min_p=0.0` / `frequency_penalty=0.0` | **完全一致** | いずれも既定 0 |

### 比較した5変種

| | temperature | top_p | top_k | presence | 備考 |
|---|---|---|---|---|---|
| **A** | 0.8 | 0.95 | 20 | **1.5** | **変更前**（temperature 以外は Modelfile 由来） |
| B | 0.7 | 0.8 | 20 | **1.5** | Qwen モデルカードの non-thinking 推奨そのまま |
| **C** | 0.7 | 0.8 | 20 | **0.0** | **採用** |
| D | 0.7 | 0.8 | 20 | 0.5 | 中間 |
| E | 0.6 | 0.8 | 20 | 0.0 | C より低温 |

B〜E は `min_p 0.0 / repeat_penalty 1.0 / frequency_penalty 0.0 / num_predict 512` を共通で明示。

### 数値（10応答の中央値。反復は最大値）

| | first token | total | 出力トークン | 文字数 | 反復指標 max |
|---|---|---|---|---|---|
| A | 205 ms | 462 ms | 22 | 35 | 0.012 |
| B | 194 ms | 494 ms | 23 | 42 | 0.068 |
| **C** | **192 ms** | **389 ms** | **18** | **30** | 0.121 |
| D | 196 ms | 474 ms | 22 | 37 | 0.076 |
| E | 191 ms | 444 ms | 24 | 40 | 0.089 |

- **first token は全変種で差が無い**（191〜205 ms、測定ノイズの幅）。
  sampling は最初のトークンまでの時間を動かさない。**レイテンシへの影響は出力長を通してのみ現れる**
- C は中央値で **73 ms 速い**。速いのは短いからであって、1トークンあたりが速いからではない
- 反復指標の max は C が最も高いが、これは技術説明で `rebase` / `merge` を繰り返す回であり、
  **劣化ではない。この指標は「同じ節を二度言う」しか見ていない**（`repeated_ratio` の docstring）

### 実際に出た文（判断の根拠はこちら）

**A（変更前）— 日本語が壊れる**

> ゆっくり休んでごきげんよろしく？
> （技術質問の締めで）整理しといて？
> 何かあったことある？

**B（`presence_penalty 1.5`）— 言語混在・冗長・絵文字**

> 作業する前に **clean な**履歴を作りたいなら rebase、そのままで OK なら merge だね。
> 早く休んで**お休みして**、しっかり寝てくれない？
> …ちょっとガッカリしたかも… 😅

`clean な` はモデルカードが警告する言語混在そのもの。絵文字は `SPEECH_PROTOCOL` 違反である
（**音声化される経路なので、読み上げられない記号が出ること自体が事故**）。

**C（採用）**

> うん、無理しないでね。少し休むのが一番だよ。
> まだ決まってない？それなら、今日はゆっくりしたほうがいいかも。
> どうしたの？何か困ってることある？

**D / E** — 壊れはしないが、C に対する利点が出なかった。D は応答が伸び、E は技術説明が
252 字まで伸びた回がある。

### `presence_penalty` を 0 にした理由

**反復は起きていなかった。** 10応答の反復指標はほぼ 0 で、罰則が防ぐはずのものが最初から無い。
一方で罰則の代償は出ている（B）。1.5 は**長い生成が無限反復に落ちるのを防ぐ値**であり、
Lumi の応答は1〜2文である。罰則は直近 `repeat_last_n`（既定 64）に加算で効くので、
短い応答では助詞・語尾・**ユーザーが今言った語**に支払われる。

### 併せて確認した — `num_ctx` は今のところ原因ではない

実測のプロンプトは **529〜620 トークン**（`prompt_eval_count`）。
ロード中のモデルの `context_length` は **4096**（Ollama の既定で、モデルの 262144 ではない）。

`agent/prompt.py` の予算は推定 3000 トークンだが、**推定器は日本語を1文字1トークンで数える**ため
実際の 5〜6倍を見積もっている。したがって現状 4096 に当たることはなく、
**persona が文脈から押し出されて文体が崩れる、という筋書きは今回の症状の原因ではない。**
記憶ブロックと tool schema が増えれば当たりうる**天井**ではある。

### 残っている、生成設定では直らないもの

| 症状 | どこの問題か |
|---|---|
| 3ターン目で語尾が男性的になる（`〜だからな` / `どうしたんだ？`） | **persona に一人称と語尾の指定が無い。** Content Pack の文言 |
| 技術説明でバッククォートや箇条書きが出る | `SPEECH_PROTOCOL` は禁じているが**遵守が揺れる。** 9B の指示追従の限界 |
| 技術的な正確さが回ごとに揺れる（rebase の説明が回によって甘い） | **9B の知識と一貫性の限界。** sampling では動かない |
