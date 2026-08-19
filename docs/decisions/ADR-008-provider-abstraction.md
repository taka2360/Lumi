# ADR-008: LLM/STT/TTS/Embedding/Vision を Provider interface で交換可能にする

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../interfaces/provider.md](../interfaces/provider.md), [../architecture/extension.md](../architecture/extension.md) |

---

## Decision

推論系のコンポーネントをすべて `Provider` interface の背後に置く。

```python
class Provider(Protocol):
    id: str
    kind: ProviderKind        # llm | stt | tts | embedding | vision
    async def load(self) -> None: ...
    async def unload(self) -> None: ...
    def resource_hint(self) -> ResourceHint: ...
```

**`load` / `unload` / `resource_hint` を Phase 1 から含める。** ただし `ModelResourceManager` の実装は Phase 5 に Deferred。

最初は Ollama / faster-whisper / AivisSpeech のみ実装する。**全 Provider を最初から実装しない。**

---

## Reason

### 3つの独立した動機がある

| 動機 | 内容 |
|---|---|
| **1. ローカル → クラウドの移行路** | 最初は無料ローカル、将来クラウド LLM。要件17 |
| **2. VRAM 戦略** | GPU に載せるもの・CPU に置くもの・別プロセスにするものを差し替えで表現する |
| **3. ライセンスリスクの緩和** | AivisSpeech / VOICEVOX / モデルのライセンス問題が判明したら差し替えるだけで済む |

**3 が実は最も実務的。** 音声ライブラリの規約は Phase 0 で確認するが、確認結果が悪くても Provider を差し替えれば設計は変わらない。

### `load` / `unload` を Phase 1 から入れる理由

Phase 1 には Vision が無いので `ModelResourceManager` は不要。しかし:

**後から Provider にライフサイクルを追加すると、全 Provider の書き換えになる。**

窓口だけ先に確保しておけば、Phase 5 は Manager を上に被せるだけで済む。Phase 1 の `load()` は「起動時に一度呼ぶ」だけでよい。

これは設計原則8（後から挿入するコストが高いものだけ先に作る）の適用。

### TTS の別プロセス CPU 化が VRAM 戦略の要

> **後続決定:** この節の CPU-only / VRAM 0 方針は、Phase 1 の実測を受けて
> [ADR-025](ADR-025-tts-on-gpu.md) が置き換えた。現在の配置は「CUDA があれば GPU、無ければ CPU」であり、
> CPU を設定で強制できる。以下は ADR-008 決定時点の記録として残す。

| モデル | 配置 | VRAM |
|---|---|---|
| LLM (Qwen3 8B Q4_K_M) | GPU / pinned | ~6.5 GB |
| STT (faster-whisper int8) | GPU / CPU | ~1.0 GB |
| VAD (Silero ONNX) | **CPU 固定** | 0 |
| Embedding (ONNX) | **CPU 固定** | 0 |
| **TTS (AivisSpeech/VOICEVOX)** | **別プロセス・CPU** | **0** |
| Vision (Phase 5) | オンデマンド | ~3-4 GB |

**RTX 4070 の実効 10.8GB に対し、TTS が 0 であることで LLM に 6.5GB を割り当てられる。**

Style-Bert-VITS2（日本語品質は最上位クラス）を選ばなかった主因はここ。GPU 4GB を占有する。

---

## Alternatives

### A. 抽象化せず、直接 SDK を呼ぶ

**利点:** コードが短い。各 SDK の機能をフルに使える
**欠点:**
- ローカル → クラウドの移行が全面書き換えになる
- **ライセンス問題が判明したときに逃げ道が無い**
- VRAM 戦略が表現できない

### B. 既存の抽象化ライブラリを使う（LiteLLM / xsAI 等）

**利点:** 実装コストが低い。多数の Provider に対応
**欠点:**
- **STT / TTS / Embedding までカバーするものが無い**（LLM 中心）
- 依存が増える
- Lumi 固有の要件（`resource_hint`、`cancel_token`、`reasoning` の分離）が表現できない

AIRI は xsAI で 40+ の LLM Provider に対応しているが、**LLM だけ**である。

### C. 最初から全 Provider を実装する

**利点:** 後で楽
**欠点:** MVP が肥大する。使わない Provider のメンテナンスコスト。**要件でも「最初から全 Provider を実装する必要はない」と明示されている**

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| 抽象化レイヤー | 各 Provider の固有機能が使いにくくなる |
| 最小公倍数問題 | Provider 間で機能差がある（Tool Calling 対応の有無など） |
| `load` / `unload` の実装義務 | Phase 1 では単純でよいが、全 Provider に要る |

### 最小公倍数問題への対処

**機能差を隠蔽しない。**

```python
class LLMProvider(Provider, Protocol):
    def supports_tools(self) -> bool: ...
    def supports_reasoning(self) -> bool: ...
```

Core は `supports_*` を見て分岐する。**「対応していない機能をエミュレートする」ことはしない**（品質が読めなくなるため）。

例外: Tool Calling 非対応の検知とプロンプトベースへのフォールバックは、実用上必要なので実装する（R6）。

---

## Consequences

### Phase 1 で実装する Provider

| kind | Provider |
|---|---|
| LLM | `OllamaProvider` |
| STT | `FasterWhisperProvider`（CTranslate2, int8。**torch 非依存**） |
| TTS | `AivisSpeechProvider`（HTTP、別プロセス） |
| VAD | `SileroVadProvider`（ONNX, CPU） |

**torch 非依存が R1（インストーラサイズ）の緩和策。** 理論上 torch は不要であり、Phase 0 で実測する。

### Phase 2 以降

| Phase | 追加 |
|---|---|
| 2 | `EmbeddingProvider`（Ruri / bge-m3、ONNX CPU） |
| 4 | `OpenAICompatProvider`（LM Studio / vLLM / クラウド） |
| 5 | `VisionProvider` + `ModelResourceManager` |

**`CharacterRenderer` は `Provider` ではない。** Stage WebView 内（TypeScript）で動くため、`runtime: stage` の別カテゴリとして扱う（→ [../architecture/extension.md](../architecture/extension.md)）。GPU は使うが、それは WebGL の描画であり `ModelResourceManager` の管理対象（推論モデル）とは別。

### `Network-optional` の枠組みで説明できる

クラウド LLM を使う場合も「LLMProvider をネットワーク能力を持つものに差し替えた」という同じ枠組みで扱う。

**「中核がクラウド化した」のではない。** これにより [DESIGN.md](../DESIGN.md) §1 のローカル性の定義が一貫する。

### 障害時は明示的に失敗する

| 障害 | 対応 |
|---|---|
| 外部エンジンが起動していない | **明示的なエラー。**「Ollama が起動していません」 |
| `load()` 失敗 | 該当 Provider を無効化。代替があれば切り替えを提案 |
| 推論中のエラー | Activity を `failed` にし、Lumi が「うまくいかなかった」と言う |

**黙って劣化しない。** 音声が出ない・返事が来ないが原因不明になるのが最悪。

### 埋め込みモデル変更への対処

`EmbeddingProvider.model_id()` と `memories.embedding_model_id` の不一致を検出し、再埋め込みが必要なことを検出する。

AIRI の telegram-bot は 1536/1024/768 の3次元を並列カラムで持つ設計だが、**Lumi は単一次元 + 再埋め込み**にする（デスクトップ単一ユーザーなら再計算コストが許容できるため）。

### `in-core ⟹ official` 制約がかかる

Provider は Core 内実行なので `trust_level: official` が必須（[ADR-005](ADR-005-extension-two-mechanisms.md)）。

**第三者製 Provider を許すかは Phase 9 で判断する。**
