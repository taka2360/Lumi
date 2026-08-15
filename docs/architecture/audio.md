# Audio Architecture — barge-in critical path

> **speech-start から TTS停止 までの critical path を Core 内に閉じる。**

親: [DESIGN.md](../DESIGN.md) / 関連: [agent.md](agent.md), [ADR-003](../decisions/ADR-003-audio-in-core.md)

---

## 1. 設計判断 — 音声 I/O を Core (Python) に置く

AIRI はブラウザ側（AudioWorklet + Web Worker + Pinia store）に置いている。Lumi は Core に置く。

### 正確な理由

**「同一プロセスだから速い」ではない。** Python の GIL・asyncio スケジューリング・オーディオバックエンドの遅延は実在する。

正しい表現:

> **speech-start から TTS停止 までの critical path を Core 内に閉じる。**

AIRI の `worklet → worker → Pinia store → pipeline` という多段経路を避けることが目的であり、プロセス数の削減が目的ではない。

### 副次的な利点

- VAD / STT / Embedding が全部 Python なので、音声をプロセス間で運ぶ必要がない
- Lumi が「今何を喋っているか」を Core が正確に知っているため、テキストレベルの自己エコー棄却が可能（EchoGuard L3）

### 代償

**AEC（音響エコーキャンセル）を自前で持つ必要がある。** ブラウザの `getUserMedia` は WebRTC の AEC を無料で提供するが、`sounddevice` は提供しない。→ §4

---

## 2. 構成

```
AudioIO
  duplex stream (sounddevice): capture + playback + playback reference channel
  │
  ├─ [audio callback スレッド]  リアルタイム制約下。推論・確保・ロックをしない
  │     capture → ring buffer に write
  │     playback ← ring buffer から read、mute_flag が立っていれば無音
  │
  ├─ [VAD スレッド]  専用 OS スレッド。asyncio を経由しない
  │     ring buffer から読む → Silero VAD (ONNX, CPU)
  │     ├─ ミュート判定（低閾値・即時）      → playback.mute_flag.set()  ★ critical path
  │     └─ 発話区間確定（高閾値・ヒステリシス）→ asyncio へ通知
  │
  ├─ [asyncio]  後片付け。遅くてよい
  │     arbiter.interrupt() / STT / TTS 生成タスクの破棄
  │
  ├─ EchoGuard          → 自分の声を自分で拾う問題への対処（§4）
  └─ PlaybackScheduler  → 文単位TTS先読み並列生成 + 順序保証再生 + 即時ミュート（§6）
```

### なぜ VAD をオーディオコールバックの中で回さないのか

**オーディオコールバックはリアルタイム制約下にある。** メモリ確保・ロック取得・GIL 待ち・推論のいずれも禁忌である。ここで Silero ONNX 推論を回すと:

- **GIL を取る**ため、asyncio スレッドや TTS 生成スレッドと競合し、バッファアンダーラン（プチプチ音）が出る
- ONNX Runtime のスレッドプール／アロケータの挙動で、まれに数十 ms のスパイクが出る。**これは p99 に直撃する**
- コールバックが締切に間に合わないと、**barge-in どころか通常の再生が壊れる**

**専用スレッドに出しても ADR-003 の要件は満たされる。** 要件は「asyncio を経由しないこと」であって「コールバック内で完結すること」ではない。ring buffer 1段の追加コストは 1フレーム分（16kHz / 32ms フレームなら最悪 32ms、実際にはフレーム到着ごとに起こすので数 ms）であり、**GIL 競合によるスパイクより遥かに小さく、分散も小さい。**

### duplex stream に reference channel を持つ理由

**Phase 2 の AEC で、再生バッファを参照信号として渡すため。** 後から duplex 化すると全面書き換えになるので、Phase 1 から duplex で開く。

### 🔴 duplex の前提が崩れる場合〔Phase 0 で実測〕

PortAudio の全二重ストリームは、**入出力が同一デバイス（少なくとも同一ホスト API・同一クロック）**であることを前提とする。

実際のユーザー環境では「USB マイク + HDMI 経由のスピーカー」「ヘッドセットマイク + デスクトップスピーカー」が普通にある。この場合:

- ストリーム生成自体が失敗する、または
- **クロックドリフトで reference channel が徐々にずれ、Phase 2 の AEC が機能しなくなる**

**Phase 0 の検証項目に「入出力が別デバイスのときの duplex 動作」を含める。** 失敗時のフォールバック（入出力を別ストリームで開き、reference はタイムスタンプで整合を取る）を Phase 0 で決めておく。Phase 2 で気づくと AEC の設計をやり直すことになる。

---

## 3. barge-in の実装要件

### critical path は asyncio を経由しない

```python
# ── audio callback スレッド（リアルタイム制約下）─────────────
def _audio_callback(self, indata, outdata, frames, time_info, status):
    """推論しない。確保しない。ロックしない。"""
    self._capture_ring.write(indata)             # lock-free
    if self._mute_flag.is_set():
        outdata[:] = 0                           # 即座に無音
    else:
        self._playback_ring.read_into(outdata)


# ── VAD スレッド（専用 OS スレッド。asyncio ではない）─────────
def _vad_loop(self):
    while not self._closing:
        frame = self._capture_ring.read_blocking()
        prob = self.vad.probability(frame)       # Silero ONNX, CPU

        # (a) ミュート判定 — 即時・低閾値・誤爆を許容する
        if prob > self.mute_threshold and self.playback.is_active():
            self._mute_flag.set()                # ★ ここが barge-in の critical path
            self._notify_asyncio("speech_maybe_started")

        # (b) 発話区間の確定 — ヒステリシス + min_speech_duration + pad
        ev = self.segmenter.feed(prob, frame)
        if ev is SpeechStartConfirmed:
            self._notify_asyncio("speech_started")
        elif ev is SpeechEnd:
            self._notify_asyncio("speech_ended", audio=self.segmenter.take())
        elif ev is FalseTrigger:
            self._mute_flag.clear()              # 誤爆だったので再生を戻す


# ── asyncio 側（後片付け。ここは遅くてよい）──────────────────
async def _drain_events(self):
    async for ev in self._events:
        if ev is SpeechStartConfirmed:
            self.arbiter.interrupt("user_speech")   # Activity cancel / LLM 中断
        elif ev is SpeechEnd:
            await self._transcribe_and_respond(ev.audio)
```

**3層の分離が重要。**

| 層 | 責務 | 締切 |
|---|---|---|
| audio callback | リング入出力とミュート適用のみ | **数 ms（ハード）** |
| VAD スレッド | 推論、ミュート判定、区間確定 | 〜30 ms |
| asyncio | Activity 調停、STT、応答生成 | 遅くてよい |

**ユーザーが体感するのは「音が止まる」まで**であり、それは上2層で完結する。

### ミュート判定と発話区間確定を分ける

**この2つは別の閾値で別々に行う。** 同じにすると、`min_speech_duration_ms = 250` を待つ間ずっと Lumi が喋り続ける（体感で 300ms 以上の被り）ことになる。

| | ミュート判定 | 発話区間の確定 |
|---|---|---|
| 目的 | **すぐ黙る** | STT に渡す区間を切る |
| 閾値 | 低い（誤爆を許容） | 高い + ヒステリシス |
| 最小継続 | **無し**（1フレームで発火） | `min_speech_duration_ms` |
| 誤爆時 | **`mute_flag` を戻して再生を再開する** | 区間を破棄 |
| 到達先 | `mute_flag`（同期） | asyncio → Arbiter |

**誤爆から復帰できる構造にしておくことで、ミュート閾値をかなり攻められる。** 「一瞬止まってすぐ戻る」は「300ms 被り続ける」より遥かに体感が良い。

### Cancellation 契約

**TTS 再生の停止は `hard`。** バッファのミュートで即座に無音化できることを実装要件とする。→ [../contracts/state-machines.md](../contracts/state-machines.md)

**このミュートは Activity 状態遷移ではないため、Arbiter を経由しない**（Invariant 4 の適用外 → [../contracts/invariants.md](../contracts/invariants.md)）。

---

## 4. EchoGuard — 3段階

**問題**: スピーカー使用時、Lumi の発話をマイクが拾い、Lumi が自分で自分を遮る。

### AIRI の対処と、その問題

AIRI は「発話中は音声入力を抑制する」という保守的な選択をしている（`isVoiceInputSuppressed`、発話終了後もクールダウン）。

これは自己ループを防ぐが、**barge-in を原理的に不可能にする**。Lumi が喋っている間は聞いていないことになる。

### Lumi の3段階

> **段階の呼称は `L1` / `L2` / `L3` とする。** プロジェクトの Phase 番号と紛らわしいため（対応関係は §10）。

| 段階 | 手法 | 内容 |
|---|---|---|
| **L1** | **適応的閾値** | 再生中は VAD の `mute_threshold` を上げる。**抑制ではなく閾値調整**であり、大きな声なら必ず割り込める。ヘッドホン前提を明示 |
| **L2** | **AEC** | `webrtc-audio-processing` 等。再生バッファを参照信号として渡す（duplex の reference channel） |
| **L3** | **テキストレベル自己エコー棄却** | Core は自分が今何を喋っているか正確に知っている。STT 結果が発話中テキストと高一致なら破棄 |

**L1 でも「抑制」はしない。** 閾値を上げるだけ。これが AIRI との決定的な違い。

### L1 のヘッドホン前提

ヘッドホンを使わない場合、barge-in が誤動作する可能性がある。**これを設定画面とドキュメントで明示する。** 黙って劣化させない。

---

## 5. VAD

**Silero VAD (ONNX Runtime, CPU)。VRAM を使わない。**

### パラメータ〔Provisional〕

AIRI の実測値を出発点にする（`packages/stage-ui/src/libs/audio/vad.ts` の既定値は実運用で調整されたもので、参考になる）。

| パラメータ | 初期値 | 用途 |
|---|---|---|
| `sample_rate` | 16000 | |
| `frame_ms` | 32 | VAD スレッドの起床間隔 |
| **`mute_threshold`** | **0.5** | **ミュート判定（即時）。誤爆を許容する** |
| `speech_threshold` | 0.3 | 発話区間の開始 |
| `exit_threshold` | 0.1 | 発話区間の終了（ヒステリシス） |
| `min_silence_duration_ms` | 400 | 区間確定 |
| `speech_pad_ms` | 80 | 区間確定 |
| `min_speech_duration_ms` | 250 | 区間確定。**ミュート判定には適用しない** |
| **`false_trigger_ms`** | **300** | **この時間内に区間が確定しなければミュートを戻す** |

**ヒステリシス（speech / exit の二重閾値）とプリロールバッファは必須。** 単一閾値だと語頭が切れる。

**`mute_threshold` が `speech_threshold` より高いのは意図的。** ミュートは「明らかに声が来た」で即座に発火させたい一方、区間の開始は語頭を取りこぼさないよう低めの閾値 + プリロールで拾う。役割が違うので値も別に持つ。

### 再生中の閾値調整（EchoGuard L1）

```python
mute_threshold = MUTE_THRESHOLD * (BOOST if self.playback.is_active() else 1.0)
```

---

## 6. TTS — 文単位セグメント化 + 先読み並列生成

AIRI の `packages/pipelines-audio` のアプローチを借用する。これは実証された良い設計。

```
LLM トークンストリーム
  ↓ 文単位にセグメント化（句読点 + 最大長）
  ↓ 最大 N 並列で TTS 生成（既定 N=4）
  ↓ シーケンス順にスケジュール
  ↓ 順序保証再生
```

### なぜ並列生成が要るのか

第1文の TTS が終わるのを待ってから第2文を生成すると、文の切れ目で必ず間が空く。先読みすることで途切れない。

### なぜ順序保証が要るのか

短い文の方が先に生成完了する。到着順に再生すると文が入れ替わる。

### 即時ミュート

**生成中のタスクは破棄し、再生中のバッファは即座にミュートする。** 生成完了を待たない。

---

## 7. レイテンシ SLO

**平均値ではなくパーセンタイルで管理する。** 音声UIは「たまに4秒待たされる」が最も体感を壊す。

### 発話終端 → 最初の音が出るまで

| | 目標 |
|---|---|
| p50 | < 1.2 s |
| p95 | < 2.0 s |
| p99 | < 3.0 s |
| Hard failure | > 5.0 s（「考え中」の表現を出す） |

### 区間別 p50 目標

| 区間 | 目標 | 備考 |
|---|---|---|
| VAD 発話終端の確定 | 0.18 s | `min_silence_duration_ms` に律速される |
| STT | 0.22 s | faster-whisper int8 |
| 記憶検索 | 0.05 s | Phase 2 以降。Phase 1 は 0 |
| プロンプト組み立て | 0.03 s | 予算計算・切り落とし・provenance 付与 |
| LLM 初トークン | 0.28 s | |
| 第1文 TTS 生成 | 0.20 s | AivisSpeech への HTTP 往復を含む |
| 再生開始（バッファ + IPC） | 0.04 s | |
| **区間合計** | **1.00 s** | |
| **予備** | **0.20 s** | EventBus の永続化・GC・スケジューリング・計測外の処理 |
| **合計** | **1.20 s** | = p50 目標 |

### なぜ予備枠を明示するのか

**区間の合計を p50 目標そのものにしてはならない。** 理由は2つ。

1. **計測していない処理が必ずある。** DomainEvent の永続化（同期トランザクション）、Provenance の join、Activity の状態遷移、WS の往復。1つ1つは小さいが、合計するとゼロではない
2. **逐次処理の合計の中央値は、各区間の中央値の和より大きい。** 各区間の分布は右に裾を引くため、「全区間が同時に中央値だった」という試行は中央値より稀である

**規則: 区間合計は p50 目標の 85% 以下に収める。** 現在 1.00 / 1.20 = 83%。区間を追加するときはこの制約を先に確認する。

Phase 1 は記憶検索が無いので区間合計 0.95 s。

### 計測

**全区間を毎回計測してログに出す。** 守れない区間が設計の見直し点になる。

```
{"event": "turn_latency", "correlation_id": "...",
 "vad_ms": 180, "stt_ms": 240, "retrieve_ms": 0, "assemble_ms": 25,
 "llm_first_token_ms": 310, "tts_first_audio_ms": 210, "playback_ms": 35,
 "measured_sum_ms": 1000, "total_ms": 1180, "unaccounted_ms": 180}
```

**`unaccounted_ms`（= `total_ms - measured_sum_ms`）を必ず出す。** これが予備枠を食い潰し始めたら、計測していない処理が増えているサイン。

### barge-in のレイテンシ

| 区間 | 定義 | 目標 |
|---|---|---|
| **mute latency** | **VAD が `mute_threshold` を超えたフレームを読んでから、出力が無音になるまで** | **< 50 ms** |
| 知覚 barge-in latency | ユーザーが発声を始めてから無音になるまで（= フレーム境界 + VAD 推論 + mute latency） | < 120 ms |

**2つを分けて計測する。** 50ms は実装が制御できる区間の目標であり、ユーザーの体感は上の行ではなく下の行で決まる。`min_speech_duration_ms` はミュート判定に適用しないので、ここには入らない（§3）。

---

## 8. デバイス

| 項目 | 扱い |
|---|---|
| 入力デバイス選択 | 設定。既定はシステム既定 |
| 出力デバイス選択 | 設定。既定はシステム既定 |
| デバイス切断 | 検知して再接続を試み、失敗したらユーザーに通知 |
| サンプルレート変換 | 必要なら Core 内で行う（VAD は 16kHz 固定） |

---

## 9. AIRI との比較

| | AIRI | Lumi |
|---|---|---|
| 配置 | ブラウザ（AudioWorklet + Worker + Pinia） | **Core (Python)** |
| barge-in | **未実装**。「発話中は入力を抑制」 | **実装。閾値調整であって抑制ではない** |
| 割り込み経路 | `stopAll('new-message')` / 手動停止 / priority preemption の3つ | ミュートは VAD スレッド、Activity 中断は Arbiter に一本化 |
| AEC | ブラウザの `echoCancellation: true` に依存 | 3段階の EchoGuard を自前で持つ |
| VAD | Silero (Transformers.js) | Silero (ONNX Runtime, Python) |
| TTS 並列生成 | あり（良い設計。借用する） | 同じアプローチ |
| SLO | なし | p50/p95/p99 を計測 |

**AIRI は barge-in に必要な部品（intent の interrupt、playbackManager.interrupt、優先度）を持っているが、VAD の `speech-start` と TTS 停止が結線されていない。** エコーキャンセルが不十分だと自己ループするため保守的な選択をしている、と読める。

Lumi はここを差別化点にする。代償として AEC を自前で持つ。

---

## 10. Phase ごとの実装範囲

| Phase | 内容 |
|---|---|
| **0** | ハードコードされた「こんにちは」を AivisSpeech で発話 → リップシンク。duplex stream の骨格。**入出力が別デバイスのときの duplex 動作を実測** |
| **1** | VAD / STT / TTS の全経路。**barge-in**（3層分離 + 2閾値）。**EchoGuard L1**。SLO 計測 |
| **2** | **EchoGuard L2**（AEC） |
| **3** | **EchoGuard L3**（テキストレベル棄却） |

---

## 11. テスト

| # | テスト |
|---|---|
| 1 | 録音済み WAV を注入するオフラインパイプラインテスト（VAD → STT） |
| 2 | 長い先頭無音、単発発話、連続2発話のケース |
| 3 | **mute latency の自動計測**（VAD がフレームを読んでから無音まで。目標 50ms 以下） |
| 3b | **知覚 barge-in latency の自動計測**（発声開始から無音まで。目標 120ms 以下） |
| 4 | 再生中に `mute_threshold` を超えると即座に無音になる |
| 4b | **`min_speech_duration_ms` がミュート判定に適用されていない**（250ms 待たない） |
| 4c | **誤爆（false trigger）でミュートが解除され、再生が再開する** |
| 5 | 再生中の閾値ブーストが効く（小さい音では割り込まない） |
| 6 | **大きい音では再生中でも必ず割り込める**（抑制していないことの確認） |
| 7 | TTS の文が順序通りに再生される（短い文が先に生成完了しても） |
| 8 | interrupt 時に生成中の TTS タスクが破棄される |
| 9 | VAD のヒステリシスで語頭が切れない |
| 10 | デバイス切断時に再接続を試み、失敗したら通知する |
| 11 | 区間別レイテンシと `unaccounted_ms` が毎ターン記録される |
| 12 | **オーディオコールバック内で推論・メモリ確保・ロック取得をしていない**（静的検査 + 実行時のコールバック所要時間の計測） |
| 13 | **負荷時（LLM 推論 + TTS 生成の同時実行）にバッファアンダーランが発生しない** |
| 14 | **入出力が別デバイスのときの duplex 動作**（Phase 0。失敗するならフォールバック経路が動く） |
