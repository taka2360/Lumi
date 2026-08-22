# Interface: Provider（LLM / STT / TTS / Embedding / Vision）

> Core 内実行。`trust_level: official` 必須。差し替え可能性がライセンスリスクの緩和策でもある。

親: [DESIGN.md](../DESIGN.md) / 関連: [../architecture/extension.md](../architecture/extension.md), [ADR-008](../decisions/ADR-008-provider-abstraction.md)

---

## 共通の基底

```python
class Provider(Protocol):
    id: str
    kind: ProviderKind           # llm | stt | tts | embedding | vision

    async def load(self) -> None: ...
    async def unload(self) -> None: ...
    def resource_hint(self) -> ResourceHint: ...
    def is_loaded(self) -> bool: ...
    def attribution(self) -> Attribution: ...


@dataclass(frozen=True)
class ResourceHint:
    device_pref: DevicePref      # gpu_required | gpu_preferred | cpu_only | external_process
    vram_estimate_mb: int        # 0 = GPU を使わない
    load_time_estimate_ms: int
    unload_policy: UnloadPolicy  # pinned | lru | on_demand


@dataclass(frozen=True)
class Attribution:
    """クレジット表示に必要な情報。Core はこれを解釈せず、そのまま Stage に渡す。"""
    display_name: str            # 例: "VOICEVOX"
    credit_text: str             # 規約が要求する表記。例: "VOICEVOX:ずんだもん"
    license_name: str            # 例: "LGPL-3.0"
    license_url: str | None      # ライセンス全文の入手先
    homepage_url: str | None
```

### `load` / `unload` / `resource_hint` を Phase 1 で入れる理由

Phase 1 には Vision が無いので `ModelResourceManager` は不要（Phase 5 に Deferred）。

しかし**後から Provider にライフサイクルを追加すると全 Provider の書き換えになる**。窓口だけ先に確保しておけば、Phase 5 は Manager を上に被せるだけで済む。

Phase 1 の `load()` に、Phase 5 の Manager のような調停は要らない。

ただし**「起動時に一度呼ぶだけ」ではない。** 実際には最初に必要になったターンが呼ぶ。
外部エンジンの起動は十数秒かかるので、**その間に来たターンが全部「まだロードされていない」を見る。**
`load()` が冪等であることは、**同時に呼ばれてよいことを意味しない**
（4 プロセス起動して VRAM を 4 GB 食った。2026-08-17 実測）。
**同時呼び出しの直列化は `ProviderRegistry.get()` の責任**（kind ごとに1本。TTS の起動待ちが STT を止めない）。

#### ★ `load()` は「接続確認」ではなく「使える状態にする」ことである / `load()` is not a connection check〔2026-08-18 実測〕

**接続確認だけで返す `load()` は、コストを最初のターンに先送りしているだけである。**
Phase 1 の3つの Provider は、全部これをやっていた。

| Provider | `load()` が実際にやっていたこと | 最初のターンが払っていた額 |
|---|---|---|
| LLM（Ollama） | `/api/tags` でモデルの**存在**を確認しただけ | **3767 ms**（重みが VRAM に無い） |
| TTS（AivisSpeech） | エンジンを起動し、既定話者の **ID を決めた**だけ | **3092 ms**（話者モデルが未初期化） |
| STT（faster-whisper） | **呼ばれてすらいなかった**（最初の発話が呼ぶ） | **2489 ms**（`stt_ms` の外側 = `unaccounted_ms` に化けていた） |

**規則: `load()` を終えた Provider は、次の呼び出しを本番レイテンシで返せなければならない。**
返せないなら、それは load できていない。

これは `is_loaded()` の意味でもある。「ロード済み」が「まだ数秒かかる」を意味するなら、
`ProviderRegistry` の直列化（表 2b）も、`resource_hint().load_time_estimate_ms` も、
Phase 5 の `ModelResourceManager` も、**すべて嘘の上に立つ。**

**「重みは外部エンジンが持っているから Lumi の責任ではない」は成立しない。** ユーザーが待つのは
どちらでも同じ 3.7 秒であり、**どのプロセスがメモリを確保したかはユーザーには見えない。**

> **これは「起動時に一度呼ぶだけではない」（上記）を否定しない。** 最初に必要になったターンが
> 呼ぶ経路は残る。変わったのは、**その経路に頼らず起動時に払っておく**ことと、
> **払い終えたと言うからには本当に払い終えていること**である。

##### ★ 「重みを載せた」も、まだ払い終えていなかった〔2026-08-22 実測〕

**上の規則は、上の表より広かった。** 重みをメモリに載せるところまで直しても、1ターン目は
まだ 4.3 秒かかっていた（内訳と検証 →
[../measurements/phase1.md](../measurements/phase1.md)「1ターン目がまた 4.3 秒だった」）。

**残っていたのは「初回の推論そのもの」の費用である。** CUDA のカーネルは遅延ロードされ、
cuBLAS は初回呼び出しで初期化される。重みが載っただけの Provider は、まだ一度も動いていない。

**規則の実装: `load()` は最後に一度だけ捨て推論を通す。** 入力は本番のものである必要はない
（STT は無音、TTS は短い1文を合成して捨てる）。「次の呼び出しを本番レイテンシで返せる」を
満たす方法が、実測上これしかない。

**暖機の失敗は `load()` の失敗ではない。** 警告を出して続行する。暖機が決めるのは
**いつ払うか**であって**払えるか**ではなく、ここを致命にすると一度きりの推論のつまずきで
boot phase が `blocked` に落ちる（[ADR-034](../decisions/ADR-034-gate-startup-on-complete-setup.md)）。

### `attribution()` を Phase 1 で入れる理由

**同じ理由**（後から Provider にメソッドを追加すると全 Provider の書き換えになる）に加えて、もう一つある。

**クレジット文字列を Core にハードコードすると、Provider を差し替えた瞬間にクレジットが嘘になる。** 「差し替え可能性がライセンスリスクの緩和策である」という主張は、差し替えたときに表示も追随して初めて成立する。

クレジット表示は Phase 0 で必要になる（配布物ができるため）。**Phase 0 の時点では Provider がまだ無いので、Stage 側のクレジット画面を先に作り、Phase 1 で `attribution()` を繋ぎ込む。** → [../licensing.md](../licensing.md) §6, [ADR-019](../decisions/ADR-019-tts-engine-distribution.md)

---

## LLMProvider

```python
class LLMProvider(Provider, Protocol):
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDescriptor] | None,
        options: LLMOptions,
        cancel_token: CancelToken,
    ) -> AsyncIterator[LLMEvent]: ...


LLMEvent = (
    TextDelta        # {"text": str}
  | ReasoningDelta   # {"text": str}       思考。TTS に流さない
  | ToolCall         # {"id", "name", "arguments"}
  | Finish           # {"reason", "usage"}
  | Error
)
```

### 実装候補

| Provider | 状態 |
|---|---|
| `OllamaProvider` | **Phase 1 で実装** |
| `OpenAICompatProvider` | Phase 4 以降。LM Studio / vLLM も兼ねる |
| `AnthropicProvider` | 将来 |
| `GeminiProvider` | 将来 |
| `OpenRouterProvider` | 将来 |

**最初から全 Provider を実装しない。** Ollama を優先する。

### Tool Calling の品質差への対処

ローカル 8B の Tool Calling、特に日本語は品質が不安定（R6）。

| 対処 | 内容 |
|---|---|
| 制約デコーディング | JSON Schema に沿った生成を強制（Ollama の grammar / structured output） |
| **タスク別モデル** | 会話用と計画用で別モデルを使えるようにする |
| フォールバック | Tool Calling 非対応を検知したら、プロンプトベースの疑似 tool call に降格 |

AIRI は `isToolRelatedError()` で10パターンの正規表現によるランタイム検知と自動デグレードを実装している。この**考え方**は借用する（実装は独自）。

### `reasoning` を TTS に流さない

思考タグ（`<think>` 等）の内容は音声化しない。AIRI の `response-categoriser` と同じ問題意識。

**`TextDelta` と `ReasoningDelta` を別の型にする。** 同じ型で「これは思考です」フラグを付けると、
後段（文分割 → TTS）が必ずどこかで取り違える。

### 宛先は 127.0.0.1 に固定する〔Phase 1 実装時に確定〕

**`OllamaProvider` はホストを設定可能にしない。** 可能にすると
「Lumi が任意のサーバに会話内容を送る機能」になる（`aivisspeech.py` と同じ理由）。

[ADR-023](../decisions/ADR-023-llm-runtime-and-model-acquisition.md) の
「検出できないケースは設定で指定させる」で足りるのは**ポートまで**。
リモートの推論サーバを使いたい場合は、ホストを可変にするのではなく
**別の `LLMProvider` を足す**（クラウド LLM と同じ枠組み）。
そうすれば「外部へ送っている」ことが Provider の選択として画面に出る。

---

## STTProvider

```python
class STTProvider(Provider, Protocol):
    async def transcribe(
        self,
        audio: AudioBuffer,          # 16kHz mono float32
        language: str | None,
        cancel_token: CancelToken,
    ) -> Transcription: ...


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str
    confidence: float | None
    segments: list[Segment] | None
```

### 実装候補

| Provider | 状態 |
|---|---|
| `FasterWhisperProvider` | **Phase 1 で実装**（CTranslate2, int8。**torch 非依存**） |
| `WhisperCppProvider` | 代替 |
| （クラウド系） | 将来。`Network-optional` の枠組みで扱う |

**torch 非依存が重要。** インストーラサイズ（R1）に直結する。

---

## TTSProvider

```python
@dataclass(frozen=True)
class SpeechAudio:
    wav: bytes
    timeline: VisemeTimeline | None   # None = 口を動かさない


class TTSProvider(Provider, Protocol):
    async def synthesize(
        self,
        text: str,
        voice: VoiceConfig,
        cancel_token: CancelToken,
    ) -> SpeechAudio: ...

    def supported_languages(self) -> frozenset[str]: ...
```

`VoiceConfig` は話者 (`speaker`)、音量 (`volume_scale`)、読み上げ速度 (`speed_scale`) を持つ。
`speed_scale` は 0.5〜2.0 の倍率で、既定値は 1.0。Provider は対応するエンジンの速度パラメータへ渡す。

> **音声だけを返す契約にできない。**〔Phase 1 実装時に確定〕
> **リップシンクのタイムラインは合成の「あと」にしか作れない**（AivisSpeech は `audio_query` で
> 音素長を返さない → [renderer.md](renderer.md)）。音声と口のタイムラインは同時に決まるので、
> 1つの結果として返す。`timeline` が `None` なら**ビセームを送らない**（口は閉じたまま）。

### 実装候補

| Provider | 言語 | 配置 | VRAM |
|---|---|---|---|
| `AivisSpeechProvider` | 日本語 | **別プロセス（HTTP）。CUDA があれば GPU、無ければ CPU** | **GPU 約 1.0 GB / CPU 0** |
| `VoicevoxProvider` | 日本語 | **別プロセス（HTTP）。CUDA があれば GPU、無ければ CPU** | **GPU 約 1.0 GB / CPU 0** |
| `KokoroProvider` | 英語ほか | Core内 or 別プロセス | 小 |

### なぜ別プロセスが重要か

別プロセス境界は、Core から TTS エンジンのライフサイクルとライセンスを分離するために維持する。
デバイス配置はその境界とは独立であり、Phase 1 では **CUDA があれば GPU、無ければ CPU** とする。
CPU は設定で強制でき、実際の配置を状態として公開する。レイテンシ SLO は GPU 構成での約束である。
詳細と実測値は [ADR-025](../decisions/ADR-025-tts-on-gpu.md) および
[measurements/phase1.md](../measurements/phase1.md) を正とする。

Style-Bert-VITS2（日本語品質は最上位クラス）は GPU 4GB を占有するため、Phase 1 の配置候補にはしない。

### ライセンス上の意味

AivisSpeech / VOICEVOX Engine は LGPL-3.0 系。**HTTP 越しの別プロセスであることが、Core（MIT）のライセンス境界を保つ前提になっている。**

ただし **「別プロセス通信だからライセンス上の影響がない」と断定はしない。**

**`TTSProvider` 抽象があること自体がリスク緩和策である。** 問題が判明したら差し替えればよい。

### 配布方針と音声ライブラリの規約

**調査結果と配布方針の唯一の定義場所は [../licensing.md](../licensing.md)。**〔2026-08-15 調査済み〕

要点のみ:

- **エンジン本体を配布物に含めない。** ユーザーの明示的な選択に基づく実行時取得 → [ADR-019](../decisions/ADR-019-tts-engine-distribution.md)
- **VOICEVOX は同梱不可**（ソフトウェア利用規約が無断再配布を明示的に禁止）
- **クレジット表記が必須**（VOICEVOX 系）。`attribution()` がその窓口
- **ACML の特例条項が Lumi に直接適用される**（LLM が任意テキストを生成して TTS に流す構成）。開発元に「現実的な範囲で努力する」義務がある → [../licensing.md](../licensing.md) §5

---

## EmbeddingProvider

```python
class EmbeddingProvider(Provider, Protocol):
    async def embed(self, texts: list[str]) -> list[Vector]: ...
    def dimension(self) -> int: ...
    def model_id(self) -> str: ...
```

### 実装候補〔Provisional。Phase 2 で日本語検索品質を実測〕

| Provider | 備考 |
|---|---|
| `RuriProvider` | 日本語特化 |
| `BgeM3Provider` | 多言語 |

**ONNX / CPU 実行。** VRAM を使わない。

### `model_id` と `dimension` が必要な理由

**埋め込みモデルを変えると、既存のベクトルが無効になる。**

`memories` テーブルに `embedding_model_id` を持たせ、モデル変更時に再埋め込みが必要なことを検出する。AIRI の telegram-bot が 1536/1024/768 の3次元を並列カラムで持っているのは同じ問題への対処だが、Lumi は単一次元 + 再埋め込みで対応する（デスクトップ単一ユーザーなら再計算コストが許容できるため）。

---

## VisionProvider〔Phase 5〕

```python
class VisionProvider(Provider, Protocol):
    async def observe(
        self,
        image: Image,
        question: str,
        cancel_token: CancelToken,
    ) -> Observation: ...
```

- 結果は必ず `ProvenanceClass = UNTRUSTED`
- `resource_hint().unload_policy = ON_DEMAND`（使用後アンロード）
- screenshot hash によるキャッシュは Provider ではなく Tool 層で行う

---

## Provider Registry

```python
class ProviderRegistry:
    def register(self, provider: Provider, *, select: bool = True) -> None: ...

    async def get(self, kind: ProviderKind) -> Provider:
        """設定で選択されている Provider を返す。**未 load なら load する**（だから async）"""

    def peek(self, kind: ProviderKind) -> Provider:
        """**load せずに**取り出す。状態表示と `attribution()` に使う"""

    def available(self, kind: ProviderKind) -> list[ProviderInfo]: ...
    def attributions(self) -> list[Attribution]:
        """クレジット画面へ。**選択されているものだけ**"""
```

### 障害時

| 障害 | 対応 |
|---|---|
| 外部エンジンが起動していない | **明示的なエラー。** ユーザーに「Ollama が起動していません」と伝える |
| `load()` 失敗 | 該当 Provider を無効化。代替があれば切り替えを提案 |
| 推論中のエラー | Activity を `failed` にし、Lumi が「うまくいかなかった」と言う |

**黙って劣化しない。** 音声が出ない、返事が来ない、が原因不明になるのが最悪。

### 例外の使い分けと `reason`

| 例外 | 意味 | ユーザーに求めること |
|---|---|---|
| `ProviderNotConfigured` | **まだ入っていない** | セットアップを実行する |
| `ProviderUnavailable` | **入っているが使えない**（起動していない / 壊れている / このデバイスでは動かない） | 起動する・直す |
| `ProviderFailed` | 推論中に失敗した | — |

**「入っていない」と「壊れている」を混ぜない。** 持っているモデルを「取得してください」と
案内するのは間違った指示であり、混ぜた時点でどちらの案内も信用できなくなる。
インストール済みかどうかを確かめた**後**の失敗は、必ず「壊れている」側である。

`ProviderError(reason, detail)` の **`reason` は機械可読な短い符号**（`model_missing` /
`model_load_failed` など）。ウォームアップのログはこれで分類するので、
**文章を入れると分類も突き合わせもできなくなる。** 事情は `detail` に書く。

---

## Phase 5: ModelResourceManager

〔Deferred。Phase 0-1 の VRAM 実測値の上に設計する〕

```python
class ModelResourceManager:
    async def acquire(self, provider: Provider) -> None:
        """VRAM を確保。必要なら LRU で他をアンロード。"""
    async def release(self, provider: Provider) -> None: ...
    def budget(self) -> ResourceBudget: ...
```

### 想定配置

**モデル配置と VRAM 見積の表は [../DESIGN.md](../DESIGN.md) §7 が唯一の定義場所。**〔Provisional。Phase 0-1 で実測して更新する〕

### `ModelResourceManager` と `inference_lease` は別物

| | 管理するもの |
|---|---|
| `ModelResourceManager`（Phase 5） | **どのモデルが VRAM に載るか** |
| `arbiter.inference_lease()`（Phase 2〜） | **誰が今推論してよいか**（foreground Activity か Job か）→ [ADR-018](../decisions/ADR-018-foreground-and-jobs.md) |

後者は Reflection Job の登場（Phase 2）から必要であり、Phase 5 を待たない。

### 明示ルール

**Vision が載らない場合は、小型 VLM に降格するか、明示的に失敗させる。** 黙って遅くならない。

GameAgent セッション中の Vision ロードは予算チェック必須。

---

## テスト

| # | テスト |
|---|---|
| 1 | 各 Provider が `Provider` protocol を満たす |
| 2 | `load` / `unload` が冪等 |
| 2b | **`load()` に時間がかかる間に `get()` が同時に来ても、`load()` は1回しか走らない**（kind ごとに直列化。別 kind は待たされない） |
| 2c | **`load()` が返った時点で、次の呼び出しにモデルのロード時間が含まれない**（LLM は重みが常駐、TTS は話者が初期化済み、STT はモデルが構築済み） |
| 2d | **3つの Provider すべてが起動時にウォームされる**（1つでも漏れると、その分が最初のターンに乗る） |
| 3 | 外部エンジン未起動時に明示的なエラーになる |
| 3b | **インストール済みのモデルが構築に失敗したとき、`model_missing` にならない**（「入っていない」と「壊れている」を混ぜない） |
| 3c | **`reason` が短い符号である**（文章を入れない。ログの分類キー） |
| 4 | LLM の `cancel_token` でストリームが中断する |
| 5 | Tool Calling 非対応の検知とフォールバックが動く |
| 6 | `reasoning` が TTS に流れない |
| 7 | 埋め込みモデル変更が検出される（`model_id` の不一致） |
| 8 | TTS の `supported_languages` に無い言語で明示的に失敗する |
| 9 | Vision の結果が `UNTRUSTED` になる |
| 10 | `resource_hint` の `vram_estimate_mb` が実測と大きく乖離しない（Phase 5） |
