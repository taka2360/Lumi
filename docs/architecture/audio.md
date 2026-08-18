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
  capture stream / playback stream（sounddevice。**別々に開く** → ADR-020）
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
  ├─ reference ring     → **再生リングに書いたサンプルそのもの。** Phase 2 の AEC の参照信号
  ├─ EchoGuard          → 自分の声を自分で拾う問題への対処（§4）
  └─ PlaybackScheduler  → 文単位TTS先読み並列生成 + 順序保証再生 + 即時ミュート（§6）
                          **実体は `agent/speech.py`。** audio/ には置かない（§6）
```

**capture と playback は別のコールバックで動く**（別ストリームなので）。
両者の整合は、**各コールバックで採る共通の実時間とフレーム番号**で取る（→ [ADR-020](../decisions/ADR-020-split-audio-streams.md)）。

### なぜ VAD をオーディオコールバックの中で回さないのか

**オーディオコールバックはリアルタイム制約下にある。** メモリ確保・ロック取得・GIL 待ち・推論のいずれも禁忌である。ここで Silero ONNX 推論を回すと:

- **GIL を取る**ため、asyncio スレッドや TTS 生成スレッドと競合し、バッファアンダーラン（プチプチ音）が出る
- ONNX Runtime のスレッドプール／アロケータの挙動で、まれに数十 ms のスパイクが出る。**これは p99 に直撃する**
- コールバックが締切に間に合わないと、**barge-in どころか通常の再生が壊れる**

**専用スレッドに出しても ADR-003 の要件は満たされる。** 要件は「asyncio を経由しないこと」であって「コールバック内で完結すること」ではない。ring buffer 1段の追加コストは 1フレーム分（16kHz / 32ms フレームなら最悪 32ms、実際にはフレーム到着ごとに起こすので数 ms）であり、**GIL 競合によるスパイクより遥かに小さく、分散も小さい。**

### なぜ duplex stream を使わないのか〔Confirmed。2026-08-15 実測 → [ADR-020](../decisions/ADR-020-split-audio-streams.md)〕

当初は「Phase 1 から duplex（capture + playback + reference channel）で開く」としていた。
**Phase 0 で実測し、2箇所で想定が外れたため撤回した。**

| 想定 | 実測 |
|---|---|
| duplex は入出力が同一デバイスなら開ける | **条件は「同一ホスト API かつ両者が受け入れる単一のレートが存在すること」。** 同一の USB ヘッドセットでもマイク 48 kHz / ヘッドホン 96 kHz で**開けない**。逆に USB マイク + HDMI モニタは**開ける** |
| 別デバイスだとクロックドリフトで reference がずれる | **分離ストリームでも有意なドリフトが観測されない**（測定分解能の数 ppm 以下 = 0.1 ms/分 程度）。duplex と有意差なし |

**したがって duplex の可否はユーザーの機材が決める。** 設計で担保できないものを
barge-in の土台にはできない。**入出力は常に別ストリームで開く。**

**reference signal はハードウェアから取らない。** Lumi は再生する音を自分で作っているので、
**再生リングに書いたサンプルそのもの**が参照信号になる（EchoGuard L3 が成立するのと同じ根拠）。

数値と測定条件 → [../measurements/phase0.md](../measurements/phase0.md)

### 保証しないこと

**ここで測ったのはアプリから見たストリームのペースであり、スピーカーから出てマイクに入るまでの実遅延ではない。**
2 ppm という値も1台の測定である。**Phase 2 の AEC は、ドリフトが無いことを前提にしてはならない**（遅延推定を必ず持つ）。

---

## 3. barge-in の実装要件

### critical path は asyncio を経由しない

```python
# ── audio callback スレッド（リアルタイム制約下）─────────────
# **入力と出力は別ストリーム**なので、コールバックも別（→ ADR-020）。
def _capture_callback(self, indata, frames, time_info, status):
    """推論しない。確保しない。ロックしない。"""
    self._capture_ring.write(indata, at=perf_counter())   # lock-free

def _playback_callback(self, outdata, frames, time_info, status):
    if self._mute_flag.is_set():
        outdata[:] = 0                           # 即座に無音
    else:
        self._playback_ring.read_into(outdata)
    # 実際に出した分を参照信号として残す。**ミュートした事実も含めて残す**
    self._reference_ring.write(outdata, at=perf_counter())


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
| `sample_rate` | 16000 | **VAD に入れる前のレート。ストリームをこのレートで開くのではない**（→ §8） |
| `frame_ms` | 32 | VAD スレッドの起床間隔 |
| **`mute_threshold`** | **0.5** | **ミュート判定（即時）。誤爆を許容する** |
| `speech_threshold` | 0.3 | 発話区間の開始 |
| `exit_threshold` | 0.1 | 発話区間の終了（ヒステリシス） |
| `min_silence_duration_ms` | 400 | 区間確定 |
| **`speech_pad_ms`** | **400** | 区間確定。**80 では語頭が消える**〔2026-08-17 実測、下記〕 |
| `min_speech_duration_ms` | 250 | 区間確定。**ミュート判定には適用しない** |
| **`false_trigger_ms`** | **300** | **この時間内に区間が確定しなければミュートを戻す** |

**ヒステリシス（speech / exit の二重閾値）とプリロールバッファは必須。** 単一閾値だと語頭が切れる。

#### ★ 「語頭」には2種類ある〔Phase 1 実装時に発見〕

| | 何を保持するか | 長さ |
|---|---|---|
| **プリロール** | 閾値を超える**前**のフレーム | `speech_pad_ms`（**400 ms**） |
| **候補** | 超えてから**区間が確定するまで**のフレーム | `min_speech_duration_ms`（**250 ms**） |

**どちらを落としても語頭が消える。** 実装では最初にプリロールだけを持ち、
候補の 250 ms を捨てていた（テストで気づいた）。

`min_speech_duration_ms` は「**確定を待つ**」ための時間であって「**捨ててよい**」時間ではない。
待っている間のフレームは、確定した瞬間に区間の先頭として復元されなければならない。

#### ★ プリロール 80 ms では足りない〔2026-08-17 実測 → [measurements/phase1.md](../measurements/phase1.md)〕

**両方を保持していても、まだ語頭が消えていた。**

原因は Silero の性質にある。**無声子音では VAD 確率が上がらない。** 上がるのは後続の母音に
入ってからで、そこから遡って 80 ms では子音そのものが区間の外にある。

実際に落ちていた語は、**すべて無声子音で始まっていた**:

| 正 | 出（`speech_pad_ms = 80`） |
|---|---|
| **ちょ**っと聞きたいことがあるんだけど | 聞きたいことがあるんだけど |
| **さっ**きの話の続きをしてもいいかな | の話の続きをしてもいいかな |
| **つく**えの上に置いてある資料を確認して | おいてある資料を確認して |

同じ音声をファイル全体で認識させると語頭は正しく出る（モデルの問題ではない）。

| `speech_pad_ms` | CER（24文） |
|---|---|
| 80 | 10.6% |
| **320 – 400** | **7.0%** |
| 480 | 7.9% |

**400 ms を採用。** プリロールは既にリングにあるフレームなので、遅延は増えない。

〔代償〕再生中に割り込まれた場合、プリロール 400 ms には **Lumi 自身の声の末尾が入りうる**。
ヘッドホン前提（§4 L1）の範囲であり、根本的な対処は EchoGuard **L3**（テキストレベルの
自己エコー棄却）が担う。

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
  ↓ TTS 生成（**逐次。既定 N=1** → 下記）
  ↓ シーケンス順にスケジュール
  ↓ 順序保証再生
```

### ★ 並列生成は「しない」〔2026-08-16 実測により変更〕

**当初は「最大 4 並列で先読み生成」としていた。実測で撤回した。**

TTS エンジンは1リクエストで**デバイスを飽和させる**（CPU なら 24 コア / GPU なら GPU）。
そこへ4本同時に投げると:

| | 実測（CPU） |
|---|---|
| 4並列の総時間 | 3.28 秒 |
| 逐次の総時間 | 3.35 秒 |

**総時間は変わらない。変わるのは1本あたりの時間で、4倍に伸びる。**
つまり並列化は**第1文を遅らせるだけ**だった（`tts_first_audio_ms` 865 → 573 ms で改善）。

**それでも「先読み」は失われていない。** N=1 は「1本ずつ、途切れずに」であって
「再生が終わるまで次を作らない」ではない。合成は実時間より速い（GPU で約 0.35x）ので、
**再生中に次が仕上がる**。文の切れ目に間は空かない。

**前提が変わったら見直すこと**: デバイスを飽和させない TTS（軽量モデル / 別マシン）なら
並列化に意味が戻る。`PlaybackScheduler(max_parallel=...)` は残してある。

### なぜ順序保証が要るのか

短い文の方が先に生成完了する。到着順に再生すると文が入れ替わる。

### ★ 第1セグメントだけ短く切る〔Phase 1 Step F〕

**「。」を待つと、その分だけ最初の音が遅れる。** そして遅れるのは
**発話全体で1回しかない、最も体感に効く場所**である（§7 の `llm_first_segment_ms`）。

| | 区切り | 理由 |
|---|---|---|
| **第1セグメント** | 「、」でも切る / 短い上限 | **最初の音を早く出す。** 1回だけの遅れを潰す |
| 第2以降 | 「。」のみ | **イントネーションを優先。** 細切れの音声は不自然 |

トレードオフを引き受けている: **第1セグメントの抑揚は犠牲になる。**
それでも、聞こえ始めるまでの沈黙の方が体感を壊す。
「間があってから自然に喋る」より「すぐ喋り出す」が勝つ、という賭けである。

**保証しないこと**: これは体感の改善であって、`llm_first_segment_ms` がゼロになるわけではない。
LLM が最初の句読点まで長く喋れば、上限文字数で切られるまで待つ。

### 即時ミュート

**生成中のタスクは破棄し、再生中のバッファは即座にミュートする。** 生成完了を待たない。

### なぜ `audio/` ではなく `agent/speech.py` に置くのか〔Phase 1 実装時に確定〕

`audio/` が持つのは**原始要素**（リング・リサンプル・VAD・ストリーム）であり、
**「文」も「ターン」も知らない。** PlaybackScheduler が知っているのは
「どの文をどの順で喋るか」「中断されたら何を捨てるか」であって、これは調停であり Core の仕事である。

実装上の理由もある。`providers/tts/aivisspeech.py` は WAV のデコードに `audio/wav` を使う。
Scheduler を `audio/` に置くと `audio → providers → audio` のパッケージ循環ができる。
**依存の向きは `agent → (audio, providers)` の一方向に保つ。**

`audio/playback.py` が持つのは `SpeakerPlayback`（リング + `mute_flag` + reference ring）まで。
**「即座に無音にできる」という能力は audio 層が提供し、「何を捨てるか」は agent 層が決める。**

---

## 7. レイテンシ SLO

**平均値ではなくパーセンタイルで管理する。** 音声UIは「たまに4秒待たされる」が最も体感を壊す。

### 発話終端 → 最初の音が出るまで

> **これは GPU 構成での約束である**〔[ADR-025](../decisions/ADR-025-tts-on-gpu.md)〕。
> STT と TTS を CPU で動かす環境では**達成しない**。
> 実測値と、CPU 構成でどうなるか → [../measurements/phase1.md](../measurements/phase1.md)

| | 目標 |
|---|---|
| p50 | < 1.2 s |
| p95 | < 2.0 s |
| p99 | < 3.0 s |
| Hard failure | > 5.0 s（「考え中」の表現を出す） |

### 区間別 p50 目標

| 区間 | ログのキー | 目標 | 備考 |
|---|---|---|---|
| VAD 発話終端の確定 | `vad_ms` | 0.18 s | `min_silence_duration_ms` に律速される |
| STT | `stt_ms` | 0.22 s | faster-whisper int8。**GPU 0.06 s / CPU 0.92 s**（実測） |
| 記憶検索 | `retrieve_ms` | 0.05 s | Phase 2 以降。Phase 1 は 0 |
| プロンプト組み立て | `assemble_ms` | 0.03 s | 予算計算・切り落とし・provenance 付与 |
| LLM 初トークン | `llm_first_token_ms` | 0.28 s | |
| **第1セグメントの完成** | `llm_first_segment_ms` | **0.07 s** | 初トークンから**TTS に渡せる単位が揃うまで**〔Phase 1 追加。下記〕 |
| 第1文 TTS 生成 | `tts_first_audio_ms` | 0.20 s | AivisSpeech への HTTP 往復を含む。**GPU 0.44 s / CPU 0.90 s**（実測。いずれも未達） |
| 再生開始（バッファ + IPC） | `playback_ms` | 0.04 s | |
| **区間合計** | | **1.07 s** | Phase 1 は記憶検索が無いので **1.02 s** |
| **予備** | | **0.13 s** | EventBus の永続化・GC・スケジューリング・計測外の処理 |
| **合計** | | **1.20 s** | = p50 目標 |

#### ★ `llm_first_segment_ms` を足した理由〔Phase 1 Step F〕

**当初の表には「初トークンが出てから TTS に渡せるまで」の行が無かった。**
暗黙に「初トークン = 第1文」を仮定していたが、そんな LLM は無い。
実際には句読点が来るまで数十トークン待つ。**この待ちが計測されないと `unaccounted_ms` に化け、
「計測していない処理が増えた」という本来の警告灯が意味を失う。**

#### ★ TTS の合成時間には固定費がある〔2026-08-16 実測〕

| | 3文字 | 17文字 |
|---|---|---|
| CPU | 863 ms | 1230 ms |
| **GPU** | **340 ms** | **346 ms** |

**CPU は約 0.8 秒の固定費 + 25 ms/文字。GPU はほぼ一定。**

帰結が2つある。

1. **第1セグメントを短く切っても TTS は速くならない。** §6 の「第1セグメントだけ短く切る」が
   効くのは `llm_first_segment_ms`（LLM 側の待ち）であって、TTS の固定費ではない
2. **GPU では合成時間が予測可能になる。** 長い文でも遅延が伸びないので、
   第1セグメントの長さを体感で決められる

数値 → [../measurements/phase1.md](../measurements/phase1.md)

#### ★ 85% 規則が Phase 2 で破れる

**規則: 区間合計は p50 目標の 85% 以下**（= 1.02 s）。

| | 区間合計 | 判定 |
|---|---|---|
| Phase 1（記憶検索なし） | 1.02 s | **ちょうど上限。余裕はない** |
| Phase 2（記憶検索 0.05 s） | 1.07 s | **89%。規則を破る** |

**Phase 2 で記憶検索を入れるとき、他の区間を縮めるか p50 目標を見直すかを決める必要がある。**
先送りにすると「気づいたら p50 を超えていた」になる。→ [../roadmap.md](../roadmap.md) Phase 2

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
 "llm_first_token_ms": 310, "llm_first_segment_ms": 70,
 "tts_first_audio_ms": 210, "playback_ms": 35,
 "measured_sum_ms": 1070, "total_ms": 1180, "unaccounted_ms": 110}
```

**区間は連続していなければならない。** 各区間の終端が次の区間の始端であり、
`vad_ms` の始端（実際に喋り終わった時刻）から `playback_ms` の終端（最初の音がリングに入った時刻）
までが `total_ms` である。**隙間を作ると、そこが `unaccounted_ms` に化けて意味が薄まる。**

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
| ホスト API | **Windows では WASAPI を明示的に選ぶ。** PortAudio の既定に任せない（MME は出力遅延 209 ms で barge-in が成立しない → [ADR-020](../decisions/ADR-020-split-audio-streams.md)） |
| 入力デバイス選択 | 設定。既定はシステム既定 |
| 出力デバイス選択 | 設定。既定はシステム既定 |
| サンプルレート | **デバイスが受け付けるレートで開く。** WASAPI 共有モードは mix format の1レートしか受け付けず、**16 kHz では開けない** |
| サンプルレート変換 | **必須。** ストリームのレート → VAD の 16 kHz を Core 内で行う（Phase 1）。方式 → [ADR-026](../decisions/ADR-026-polyphase-resampler.md) |
| デバイス切断 | 検知して再接続を試み、失敗したらユーザーに通知 |

### ★ サンプルレート変換は STT 精度の一部である〔2026-08-17 実測 → [ADR-026](../decisions/ADR-026-polyphase-resampler.md)〕

**変換品質は「音質の話」ではなく「認識精度の話」だった。**

初期実装（3-tap 移動平均 + 線形補間）は、8 kHz より上の成分を **−6 dB 程度しか落とさずに
音声帯域へ折り返していた**（10 kHz → 6 kHz が −7.2 dB）。日本語の摩擦音は 5〜12 kHz に
エネルギーの大半があるため、**発話のたびに摩擦音の鏡像が子音識別帯域に重なっていた。**

さらに VAD スレッドは 32 ms ごとに変換関数を呼ぶため、**状態を持たない変換器はチャンク境界で
毎秒 31 回フィルタを再スタートしていた**（一括変換に対し SNR 22.8 dB）。

| | 修正前 | 修正後 |
|---|---|---|
| 折り返し（9–12 kHz → 4–7 kHz） | −6 〜 −11 dB | **−48 dB 以下** |
| チャンク境界（一括変換との SNR） | 22.8 dB | **166 dB** |
| コスト / 32 ms チャンク | — | **0.024 ms** |

決まったこと:

- 変換は **polyphase FIR**（Kaiser 窓、128 taps/phase）。外部ライブラリを足さない（LGPL / R1）
- **連続ストリームには `StreamingResampler` を使う。** 1チャンクずつ純関数を呼ばない
- 通過域は 7 kHz で −1.15 dB 落ちる。**遷移帯域はどこかに要る**ので、これは意図した交換

### 音声入力のデバッグ〔Phase 1〕

**「認識が悪い」は2つの全く別の故障に分かれる**——STT に渡る音声が既に壊れているのか、
音声は正常でモデル / デコード設定が悪いのか。**どちらなのかを示すログは1行も無い。**

確定した発話区間を `<data_dir>/debug/stt/` に **16 kHz mono WAV + 認識結果の .txt** で
書き出す（`lumi/audio/dump.py`）。

| 実行形態 | 既定 | 上書き |
|---|---|---|
| ソースから実行（`pnpm dev` / `uv run lumi-core`） | **有効** | `LUMI_DEBUG_STT_DUMP=0` |
| 固めた配布物 | **無効** | `LUMI_DEBUG_STT_DUMP=1` |

**ソースから動かしている人は、それを開発している人である。** 音声入力で最も効く診断が、
覚えていないと使えないフラグの向こう側にあってはならない。配布された Lumi は別の話で、
**頼まれない限り何も録らない。**

上の折り返しも、語頭欠落も、**Core が出すどの指標にも現れなかった。聞けば1秒で分かった。**

### 開通したと言える条件〔2026-08-15 実測〕

**`open()` の成功では足りない。** 無効化されたエンドポイントは開けるのに**フレームが1つも来ない**
（実測したマシンの Realtek マイク端子がこれだった）。

**最初のフレームが規定時間内に届いて初めて「聞けている」と扱う。** 届かなければ明示的に失敗させる。
「聞いているつもりで無音を聞き続ける」は、黙って劣化する典型である。

### 入力デバイスが1つも無い場合

**実在する**（実測したマシンの初期状態がこれだった。録音デバイスが1つも有効でなかった）。

この場合 Lumi は**起動し、音声入力が無いことを明示する。** TTS 未セットアップと同じ扱いで、
**壊れているのではない状態**として表現する（→ [setup.md](setup.md)）。

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
| **0** | ハードコードされた「こんにちは」を AivisSpeech で発話 → リップシンク。**デバイス選択の骨格と、ストリームの開き方の実測**（→ [ADR-020](../decisions/ADR-020-split-audio-streams.md)） |
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
| 4d | **barge-in が2回目以降も効く**（区間が確定したミュートは自動では戻らない。`resume()` を呼ぶ経路があること） |
| 5 | 再生中の閾値ブーストが効く（小さい音では割り込まない） |
| 6 | **大きい音では再生中でも必ず割り込める**（抑制していないことの確認） |
| 7 | TTS の文が順序通りに再生される（短い文が先に生成完了しても） |
| 7b | **文の分割が終端を待たない**（閉じ記号を待つと第1文が1チャンク分遅れる → §6） |
| 8 | interrupt 時に生成中の TTS タスクが破棄される |
| 8b | **中断で再生キューが捨てられる**（古い音が後から再開しない） |
| 8c | **1文の TTS 失敗が発話全体を落とさず、失敗として数えられる**（黙って飛ばさない） |
| 9 | VAD のヒステリシスで語頭が切れない |
| 9b | **プリロール（確定前）と候補（確定まで）の両方が区間の先頭に残る** |
| 10 | デバイス切断時に再接続を試み、失敗したら通知する |
| 11 | 区間別レイテンシと `unaccounted_ms` が毎ターン記録される |
| 12 | **オーディオコールバック内で推論・メモリ確保・ロック取得をしていない**（静的検査 + 実行時のコールバック所要時間の計測） |
| 13 | **負荷時（LLM 推論 + TTS 生成の同時実行）にバッファアンダーランが発生しない** |
| 14 | **入出力が別デバイスでも capture / playback が開ける**（Phase 0 で実測済み。別マシンでも確認する） |
| 15 | **ホスト API の選択が WASAPI を選ぶ**（純粋関数。MME しか無い環境では MME を選び、遅延を警告する） |
| 16 | **16 kHz で開けないデバイスでも、デバイス既定のレートで開いて 16 kHz にリサンプルできる** |
| 17 | **フレームが来ないデバイスを開通失敗として扱う**（`open()` は成功する。タイムアウトで失敗させる） |
| 18 | **入力デバイスが1つも無い環境で Lumi が起動し、その状態が明示される** |
