# ADR-027: STT の既定モデルを large-v3-turbo にする

Status: Accepted
Date: 2026-08-17

| 関連 | |
|---|---|
| 修正する決定 | [ADR-023](ADR-023-llm-runtime-and-model-acquisition.md)（取得方法は変えない。**選ぶモデルだけを変える**） |
| 設計 | [DESIGN.md](../DESIGN.md) §7（VRAM 配分）, [architecture/audio.md](../architecture/audio.md) §7（SLO） |
| 前提 | [ADR-026](ADR-026-polyphase-resampler.md)（リサンプラ修正後の測定である） |
| 実装 | `core/lumi/setup/models.py`, `core/lumi/agent/runtime.py` |
| 実測 | [measurements/phase1.md](../measurements/phase1.md) |

## Decision

STT の既定モデルを **`small` から `large-v3-turbo`（`dropbox-dash/faster-whisper-large-v3-turbo`、MIT）** に変える。
compute type は **`int8_float16`** のままとする。

`small` は `STT_MODELS` に残す。**`LUMI_STT_MODEL=small` が戻り道として機能し続ける。**

## Reason

### `small` の語彙混同が実用に耐えなかった

[ADR-026](ADR-026-polyphase-resampler.md) と `speech_pad_ms` の修正で**語頭の欠落は消えたが、
残った誤りが語彙の取り違えだった。** これはパラメータで消える種類の誤りではない。

同一の発話区間（実パイプラインで切ったもの、24文）での比較:

| | `small` | **`large-v3-turbo`** |
|---|---|---|
| CER | 7.2% | **3.6%** |
| 誤りのあった文 | 8 / 24 | **3 / 24** |
| p50 | 88 ms | 143 ms |
| p95 | 100 ms | **149 ms** |
| VRAM | 424 MiB | **1024–1243 MiB** |
| ロード（warmup 込み） | 0.8 s | 2.4 s |
| ダウンロード | 484 MB | **1.62 GB** |

**誤りが半分以下になる。** 残った3件のうち2件は再測定で正しく出たので、
実際の内容誤りはこの数字より低い（CER には句読点差も入っている）。

### 予算の内側に収まる

- **`stt_ms` の予算は 0.22 秒**（[audio.md](../architecture/audio.md) §7）。p95 149 ms は**内側**
- **VRAM は DESIGN.md §7 の見積 ~1.0 GB にもともと収まっている。** 実測が 0.4 GB だっただけで、
  枠を広げるのではなく**確保していた枠を使う**話である。合計 6.9 → 7.5 / 12 GB

### `int8_float16` を維持する

`float16` と比べて **CER は同じ 3.6%**、p50 は 143 対 150 ms で**むしろ速く**、
**VRAM は半分**（1.0 対 1.9 GB）。重みを float16 で持つ理由が1つも無い。

### repo 名

faster-whisper 内部の `_MODELS` は `mobiuslabsgmbh/faster-whisper-large-v3-turbo` を指すが、
**その組織は改名済み**で、HuggingFace API はどちらの名前でも同じ id
（`dropbox-dash/faster-whisper-large-v3-turbo`）と同じ commit を返す〔2026-08-17 確認〕。
**実在する方を pin する。**

## Alternatives

| 案 | 利点 | 採らなかった理由 |
|---|---|---|
| **`small` のまま `beam_size` を上げる** | ダウンロードも VRAM も増えない | **測定した。効果が誤差に埋もれる**（走らせるたびに beam 1 と 5 の順位が入れ替わる）。語彙混同は探索幅の問題ではない |
| **`medium`** | turbo より小さい | **turbo より遅く、精度も下**（turbo は large-v3 のデコーダを 32 層 → 4 層にしたもので、エンコーダは large-v3 のまま）。中間として選ぶ理由が無い |
| **`large-v3`（非 turbo）** | 最高精度 | デコーダ 32 層。**遅延が数倍**になり `stt_ms` の予算を割る。精度差は日本語会話では小さい |
| **`distil-large-v3`** | 速い | **英語専用。** 日本語に使えない |
| **CPU に置く** | VRAM 0 | 実測 916–984 ms（`small` で）。**予算 0.22 秒に届かない**（ADR-025 と同じ結論） |

## Trade-offs

**受け入れるコスト**

- **ダウンロードが 484 MB → 1.62 GB（3.3倍）。** 初回セットアップの待ち時間が延びる
- **VRAM +0.6〜0.8 GB。** 合計 7.5 / 12 GB。Phase 5 の Vision（3-4 GB）を載せると窮屈になる
- p50 +55 ms。`stt_ms` の予算内だが、`total_ms` p50 はその分伸びる（1.50 → 約 1.55 秒）
- コールドロード 0.8 → 2.4 秒

**得るもの**

- **CER が半減する**（7.2% → 3.6%）
- 残る誤りが「語頭が消える」から「たまに固有名詞を外す」に変わる。
  **後者は会話として成立するが、前者は成立しない**

**保証しないこと**: 測定は AivisSpeech の合成音声である。**実マイク・実話者での WER は測っていない。**
話者性・環境雑音・マイク特性のいずれもここには入っていない。

## Consequences

- `DEFAULT_STT_MODEL`（`agent/runtime.py`）と `STT_ARTIFACT`（`setup/coordinator.py`）の**両方**が変わる。
  **この2つがずれると、セットアップは取得済みのモデルをもう一度提案し、何もエラーにならない。**
  テストで固定した（`test_the_fetched_model_is_the_one_the_provider_will_look_for`）
- `ResourceHint.vram_estimate_mb` をモデル名で引くようにした。**上振れ側の実測値を返す**
- pin するファイル構成が `small` と違う（`vocabulary.json` / `preprocessor_config.json`）。
  `ModelArtifact` はもともとファイル単位の pin なので、型の変更は要らなかった
- DESIGN.md §7 の VRAM 実測列を 0.4 → 1.0-1.2 GB に更新する
- **VRAM が本当に足りなくなったときの戻り道は `LUMI_STT_MODEL=small`。** 消さずに残す
