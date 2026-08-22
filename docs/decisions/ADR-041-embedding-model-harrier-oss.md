# ADR-041: 埋め込みモデルを Harrier-OSS-v1 270M に決め、`EmbeddingProvider` をクエリと文書で非対称にする

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-22 |
| Related | [ADR-004](ADR-004-sqlite-vec-memory.md), [ADR-023](ADR-023-llm-runtime-and-model-acquisition.md)（取得方式の前例）, [../architecture/memory.md](../architecture/memory.md) §2, [../interfaces/provider.md](../interfaces/provider.md), [../interfaces/memory.md](../interfaces/memory.md), [../licensing.md](../licensing.md), [../measurements/phase2.md](../measurements/phase2.md), [../roadmap.md](../roadmap.md) Phase 2e |

## Context

[memory.md](../architecture/memory.md) §2 は埋め込みモデルを **「Ruri v3系 または bge-m3 を ONNX / CPU 実行」〔Provisional〕**
とし、**Phase 2 で日本語検索品質を実測して決める**としていた。2e はこれを決めないと始まらない。

決めないと始まらない理由は2つある。

1. **`vec0` は作成時に次元を固定する。** 2c でベクトル表を作らず 2e に送ったのはこのため
2. **`embedding_model_id` が一致しないベクトルは無効になる。** 後から変えると全再埋め込みが要る

制約は Phase 1 から変わっていない。**Core は torch に依存しない**（インストーラサイズ R1）、
**VRAM は LLM に全振りするので埋め込みは CPU**、**Core = MIT の境界を侵さない**。

## Decision

### 1. モデルは `microsoft/harrier-oss-v1-270m`（ONNX 版 `onnx-community/harrier-oss-v1-270m-ONNX`）

| | |
|---|---|
| ライセンス | **MIT**（重みも ONNX 変換も） |
| 次元 | **640** |
| 文脈長 | 32k |
| プーリング | **last-token pooling + L2 正規化**（ONNX グラフの中で済んでおり、出力は `sentence_embedding`） |
| 量子化 | **q4**（`onnx/model_q4.onnx` + `.onnx_data`、196 MiB） |
| 実行 | **ONNX Runtime / CPU**。torch は要らない |

**取得は実行時**。[ADR-023](ADR-023-llm-runtime-and-model-acquisition.md) の STT モデルと同じ仕組みに載せる
（`ModelArtifact` に revision と**ファイルごとの SHA-256** を pin し、HuggingFace 以外からは取らない）。
配布物には含めない。

### 2. `EmbeddingProvider` を **クエリと文書で別のメソッドにする**

```python
class EmbeddingProvider(Provider, Protocol):
    async def embed_query(self, text: str) -> Vector: ...
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...
```

**`embed(texts)` 一本にしない。** このモデルはクエリにだけ指示文を付ける非対称な使い方を要求する。

```text
クエリ:  "Instruct: {task}\nQuery: {text}"
文書:    "{text}"        ← 指示文を付けない
```

対称な API を残すと、**間違えても例外は出ず、静かに検索品質だけが落ちる**。
呼び分けを型で強制するのがいちばん安い防御である。

### 3. 文書は `subject: content` で埋め込む

記憶レコードは本文だけでなく subject を前置して埋め込む（`user.pet: ユーザーは猫を飼っている`）。
実測で recall@3 が 90% → 100% になった（下記）。

### 4. 既定の指示文は英語で、Core が持つ

```text
Instruct: Given a message from the user, retrieve memories about the user that are relevant to it
Query: {ユーザーの発話}
```

**これは表示文字列ではないので i18n を通さない**（人格プロンプトと同じ扱い）。
日本語の指示文でも同等だったが（下表）、モデルカードの例が英語であり、**学習時の分布に近い方を既定にする**。

## Reason

### 実測（2026-08-22 / このマシン・CPU 4 threads・日本語10件 × 10クエリ）

**量子化の選択:**

| variant | サイズ | ms/query | fp32 との top1 一致 | 平均 \|Δcos\| |
|---|---|---|---|---|
| fp32 | 1383 MiB | 50 | — | — |
| int8 | 328 MiB | **187** | 100% | 0.0004 |
| **q4** | **196 MiB** | **52** | 100% | 0.0104 |
| q4f16 | 164 MiB | 59 | 100% | 0.0104 |

**int8 がいちばん遅い。** ONNX Runtime の CPU で、この decoder の int8 経路は fp32 より
3.6 倍遅かった。「量子化＝速い」という直感がここでは成り立たない。
**q4 は fp32 と同じ速さで 1/7 のサイズ**であり、順位も一致した。

**指示文と文書形式:**

| task | 文書 | top1 | recall@3 |
|---|---|---|---|
| memory 用（英語） | **`subject: content`** | **90%** | **100%** |
| memory 用（英語） | 本文のみ | 80% | 90% |
| memory 用（日本語） | 本文のみ | 90% | 90% |
| `web_search_query`（モデル同梱） | 本文のみ | 90% | 90% |
| 指示文なし | 本文のみ | 90% | 90% |

**10件 × 10クエリは「動くことの確認」であって、ベンチマークではない。** そう扱う。

### 非対称であることを型に出す理由

指示文を付け忘れたクエリは**エラーにならない**。ベクトルは出るし、検索も動くし、
それらしい結果も返る。**壊れたことに気づく手段が「なんとなく検索が悪い」しかない。**
Lumi は記憶で差別化すると決めている以上、ここが静かに劣化する経路を残せない。

### なぜ「多言語 SOTA」を日本語特化より優先したか

Ruri v3 は日本語特化で、bge-m3 は多言語である。Harrier はその後に出た多言語モデルで、
**MTEB v2 多言語で当時 SOTA**（270M で 66.5）。決め手は品質そのものより次の3つ。

1. **MIT。** Core の境界を侵さない（Ruri v3 は Apache-2.0 で問題ないが、bge-m3 系は派生元の条件を追う必要がある）
2. **ONNX 変換が公式コミュニティにあり、プーリングまでグラフに入っている。** 自前で last-token pooling を書く必要がない
3. **270M で 196 MiB。** LLM の VRAM を一切食わず、STT モデル（1.6 GB）より軽い

## Alternatives

| 案 | 利点 | 採らなかった理由 |
|---|---|---|
| **Ruri v3（日本語特化）** | 日本語のみなら強い。ONNX 化事例も多い | 会話には英語の固有名詞・コード・アプリ名が混ざる。**多言語で落ちないことの方が効く** |
| **bge-m3** | 実績が多く、multi-vector も使える | 1024 次元・550M で重い。**プーリングを自分で書く**必要がある |
| **int8 を既定にする** | fp32 に最も近い（Δcos 0.0004） | **187 ms/query。** ターン予算（[audio.md](../architecture/audio.md) §7）に対して重すぎる |
| **fp32 を既定にする** | いちばん速く、基準そのもの | **1383 MiB。** 品質差が観測できない量子化に対して、この差は割に合わない |
| `embed(texts)` の対称 API のまま、呼び出し側で指示文を付ける | 既存の interface を変えなくてよい | **付け忘れが静かに通る。** 検索品質は落ちるが、失敗として観測できない |
| モデルを配布物に同梱する | 初回起動が速い | R1（インストーラサイズ）。STT と同じ判断（ADR-023） |

## Trade-offs

**受け入れるコスト:**

- **埋め込み 1 回あたり 50 ms 前後が会話のクリティカルパスに乗る。** 検索を挟むターンは
  その分だけ最初のトークンが遅れる（[audio.md](../architecture/audio.md) §7 に行を足して実測する）
- **196 MiB の追加ダウンロード。** 記憶が使えるようになるまでの初回コストが増える
- **モデルを変えたら全再埋め込み。** `embedding_model_id` の不一致で検出する（設計どおり）
- **q4 は fp32 と完全には一致しない**（平均 \|Δcos\| 0.0104）。順位が入れ替わる余地はある

**得るもの:**

- **torch なしで意味検索が成立する。** Core の依存は ONNX Runtime と tokenizers だけ（どちらも既にある）
- **VRAM を 1 バイトも使わない。** LLM の取り分が減らない
- **MIT。** 配布物のライセンス面が STT / TTS より単純になる

## Consequences

| 変わるもの | 内容 |
|---|---|
| [memory.md](../architecture/memory.md) §2 | Embedding 節の〔Provisional〕を解消。**次元 640 が確定**し、`vec0` を作れるようになる |
| [interfaces/provider.md](../interfaces/provider.md) | `EmbeddingProvider` が `embed_query` / `embed_documents` の2本になる |
| [interfaces/memory.md](../interfaces/memory.md) | `VectorStore.dimension()` は 640 を返す。`embedding_model_id` に pin 済みの id が入る |
| [licensing.md](../licensing.md) §4.7 | 実行時取得する外部資産に埋め込みモデルの行が増える（MIT / クレジット義務なし） |
| [setup.md](../architecture/setup.md) §3b | `ModelArtifact` に埋め込みモデルが加わる。**取得できなくても会話は続く**（記憶検索だけが止まる） |
| [contracts/privacy.md](../contracts/privacy.md) §2 | 行 3（ベクトル）が実体を持つ。モデル本体は行 8（セットアップ済みの外部資産） |

### 保証しないこと

- **検索品質の一般的な保証はしない。** 実測は日本語10件の smoke であり、実運用の規模でも同じ
  順位になるとは言っていない。Phase 2 の完了条件（「数日使って正しく思い出す」）で確かめる
- **q4 が fp32 と同じ結果を返すとは言わない。** 順位一致は上記の小さな集合で観測しただけである
- **指示文の文言が最適だとは言わない。** 候補の中で最良だっただけで、Phase 2 の使用実感で見直す
