# ADR-003: 音声 I/O を Core 側に置き、barge-in critical path を Core 内に閉じる

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 改訂 | 2026-08-15 — 実装スケッチのみ差し替え（VAD をコールバック外の専用スレッドへ）。決定は変更なし |
| 関連 | [../architecture/audio.md](../architecture/audio.md), [../architecture/agent.md](../architecture/agent.md) |

---

## Decision

マイク入力・VAD・音声再生を**すべて Core（Python）内に置く**。

**speech-start から TTS 停止までの critical path を Core 内に閉じる。**
VAD コールバックから再生停止までは **asyncio のイベントループを経由しない**。

AIRI はこれをブラウザ側（AudioWorklet + Web Worker + Pinia store）に置いている。Lumi は異なる選択をする。

---

## Reason

### barge-in が中核要件であるため

Lumi の要件に「AI が喋っている途中にユーザーが話しかけたら止まる」がある。これは後付けが最も難しい機能で、Attention Arbiter の設計にも影響する。

**AIRI は barge-in を実装していない。** 逆に「発話中は音声入力を抑制する」保守的な設計を採っている（`isVoiceInputSuppressed`）。部品（intent の interrupt、playbackManager.interrupt、優先度）は揃っているのに結線されていない。

### 「同一プロセスだから速い」ではない

これは重要な訂正である。Python の GIL・asyncio スケジューリング・オーディオバックエンドの遅延は実在する。**プロセスが1つだから自動的に速いわけではない。**

正しい理由は:

> **AIRI の `worklet → worker → Pinia store → pipeline` という多段経路を避ける。**

段数が増えるほどレイテンシの分散が大きくなり、p95/p99 が悪化する。critical path を短く保つことが目的であり、プロセス数の削減は手段でも目的でもない。

### 実装要件が明確になる

> **【2026-08-15 改訂】** 当初の実装スケッチはオーディオコールバック内で VAD 推論を回していた。
> **これは誤りである。** コールバックはリアルタイム制約下にあり、ONNX 推論は GIL 競合とスパイクを生んでバッファアンダーランを起こす。
> **決定（音声 I/O を Core に置く / critical path が asyncio を経由しない）は変わらない。** 実装スケッチのみ差し替える。詳細 → [../architecture/audio.md](../architecture/audio.md) §2-3

```python
# ── audio callback スレッド（リアルタイム制約下）─────────────
def _audio_callback(self, indata, outdata, frames, ...):
    """推論しない。確保しない。ロックしない。"""
    self._capture_ring.write(indata)          # lock-free
    if self._mute_flag.is_set():
        outdata[:] = 0                        # ← 即座に無音
    else:
        self._playback_ring.read_into(outdata)


# ── VAD スレッド（専用 OS スレッド。asyncio ではない）─────────
def _vad_loop(self):
    while not self._closing:
        frame = self._capture_ring.read_blocking()
        if self.vad.probability(frame) > self.mute_threshold:
            self._mute_flag.set()             # ← 共有フラグ。同期。asyncio を待たない
            self._notify_asyncio(...)
```

**「音が止まる」は上の2層で完結し、「Activity が cancel される」は非同期でよい。** ユーザーが体感するのは前者。

**要件は「asyncio を経由しないこと」であって「コールバック内で完結すること」ではない。** ring buffer 1段の追加は数 ms であり、GIL 競合によるスパイクより遥かに小さく、分散も小さい。

この分離が可能なのは、VAD と再生が同じプロセスの同じメモリ空間にあるため。

### 副次的な利点

- VAD / STT / Embedding が全部 Python なので、音声をプロセス間で運ぶ必要がない
- **Core は自分が今何を喋っているか正確に知っている**ため、テキストレベルの自己エコー棄却が可能（EchoGuard Phase 3）

---

## Alternatives

### A. ブラウザ側に置く（AIRI の選択）

**利点:**
- **`getUserMedia` の WebRTC AEC が無料で使える**（これが大きい）
- Web Audio API が成熟している
- デバイス管理をブラウザに任せられる

**欠点:**
- critical path が worklet → worker → store → pipeline と長い
- 音声データを STT に渡すためにプロセス跨ぎが発生する（Core が Python の場合）
- **AIRI が実際にこの構成で barge-in を実装できていない**

### B. Shell（Rust）に置く

**利点:** リアルタイム性が最も高い。`cpal` 等
**欠点:** VAD / STT が Python にあるため、音声を Core に運ぶ必要がある。critical path が Shell↔Core を跨ぐ

### C. 入力は Core、出力はブラウザ

**利点:** 再生の実装が楽
**欠点:** **barge-in の critical path が分断される。** 最悪の選択

---

## Trade-offs

### 受け入れる最大のコスト — AEC を自前で持つ

ブラウザの `getUserMedia` は WebRTC の音響エコーキャンセルを無料で提供する。`sounddevice` は提供しない。

**スピーカー使用時、Lumi の発話をマイクが拾い、Lumi が自分で自分を遮る。**

### 緩和 — 3段階の EchoGuard

| Phase | 手法 | 内容 |
|---|---|---|
| **1** | 適応的閾値 | 再生中は VAD の入力閾値を上げる。**抑制ではなく閾値調整**であり、大きな声なら必ず割り込める。ヘッドホン前提を明示 |
| **2** | AEC | `webrtc-audio-processing` 等。再生バッファを参照信号として渡す |
| **3** | テキストレベル棄却 | STT 結果が発話中テキストと高一致なら破棄 |

**Phase 1 でも「抑制」はしない。閾値を上げるだけ。** これが AIRI との決定的な違い。

### duplex stream を Phase 1 から開く

Phase 2 の AEC で再生バッファを参照信号として渡すため、**Phase 1 から duplex（capture + playback + reference channel）で開く**。後から duplex 化すると全面書き換えになる。

---

## Consequences

### レイテンシ SLO を計測する義務が生じる

| | 目標 |
|---|---|
| p50 | < 1.2 s |
| p95 | < 2.0 s |
| p99 | < 3.0 s |

**平均ではなくパーセンタイル。** 音声UIは「たまに4秒待たされる」が最も体感を壊す。

barge-in のレイテンシ（speech-start → 再生停止）も自動計測する。目標 50ms 以下。

### Cancellation 契約が必要になる

**TTS 再生の停止は `hard`。** バッファのミュートで即座に無音化できることを実装要件とする（[ADR-012](ADR-012-cancellation-contract.md)）。

### Phase 1 でヘッドホン前提を明示する

ヘッドホンを使わない場合、barge-in が誤動作しうる。**設定画面とドキュメントで明示する。黙って劣化させない。**

### この判断を見直す条件

Phase 1-2 で AEC が実用水準に達しない場合、以下を再検討する。

1. マイク入力だけをブラウザ側に戻し、AEC 済みのストリームを Core に送る（critical path は長くなるが AEC は無料になる）
2. `PlatformShell` 経由で Rust 側の AEC 実装を使う

**その場合は新しい ADR を書く。**
