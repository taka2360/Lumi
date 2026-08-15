---
paths:
  - "core/lumi/audio/**/*.py"
---

# Audio — barge-in critical path

設計 → [architecture/audio.md](../../docs/architecture/audio.md), [ADR-003](../../docs/decisions/ADR-003-audio-in-core.md)

**barge-in は Lumi の中核的差別化点。絶対に落とさない。**

## 3層に分ける。層を跨がせない

| 層 | やること | やってはいけないこと | 締切 |
|---|---|---|---|
| **audio callback** | ring buffer への write / read、`mute_flag` の適用 | **推論・メモリ確保・ロック取得** | 数 ms（ハード） |
| **VAD スレッド**（専用 OS スレッド） | Silero 推論、ミュート判定、発話区間確定 | asyncio を待つこと | 〜30 ms |
| **asyncio** | `arbiter.interrupt()`、STT、応答生成 | — | 遅くてよい |

```python
def _audio_callback(self, indata, outdata, frames, ...):
    """推論しない。確保しない。ロックしない。"""
    self._capture_ring.write(indata)
    if self._mute_flag.is_set():
        outdata[:] = 0
    else:
        self._playback_ring.read_into(outdata)
```

**コールバック内で ONNX 推論を回さない。** GIL 競合とスパイクでバッファアンダーラン（プチプチ音）が出て、
barge-in どころか通常の再生が壊れる。

要件は「**asyncio を経由しないこと**」であって「コールバック内で完結すること」ではない。

## ミュート判定と発話区間確定は別物

| | ミュート判定 | 発話区間の確定 |
|---|---|---|
| 目的 | **すぐ黙る** | STT に渡す区間を切る |
| 閾値 | `mute_threshold`（高め・即時） | `speech_threshold` + ヒステリシス |
| 最小継続 | **無し**（1フレームで発火） | `min_speech_duration_ms` |
| 誤爆時 | **`mute_flag` を戻して再生を再開** | 区間を破棄 |
| 到達先 | `mute_flag`（同期） | asyncio → Arbiter |

**`min_speech_duration_ms` をミュート判定に適用しない。** 250ms 待つ間ずっと Lumi が喋り続けることになる。
**誤爆から復帰できる構造にする。** そうすればミュート閾値を攻められる。

## Arbiter との関係

- **「音が止まる」は VAD スレッドが同期的に行う。Arbiter を経由しない**（Activity 状態遷移ではないので Invariant 4 の対象外）
- 「Activity が止まる」は asyncio 側で `arbiter.interrupt()` が行う
- **TTS 再生の停止は `hard`。** バッファのミュートで即座に無音化できること

## EchoGuard — 「抑制」しない

段階は **L1 / L2 / L3**（プロジェクトの Phase 番号と混同しない）。

- **L1 でも入力を抑制しない。閾値を上げるだけ。** 大きな声なら必ず割り込める
- AIRI の「発話中は音声入力を抑制」は barge-in を原理的に不可能にする。**同じ選択をしない**

## SLO

- 区間別予算の定義は [architecture/audio.md](../../docs/architecture/audio.md) §7
- **区間合計は p50 目標の 85% 以下**に収める。予備枠を食い潰さない
- 毎ターン `unaccounted_ms` を出す。これが増えたら計測していない処理が増えている
- **mute latency**（VAD がフレームを読んでから無音まで、< 50ms）と
  **知覚 barge-in latency**（発声開始から無音まで、< 120ms）を分けて計測する

## TTS

文単位セグメント化 → 最大 N 並列で先読み生成 → **シーケンス順に再生**。
短い文が先に生成完了するので、到着順に再生すると文が入れ替わる。
interrupt 時は**生成中のタスクを破棄し、再生中バッファを即ミュート**。生成完了を待たない。
