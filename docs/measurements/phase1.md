# Phase 1 実測値

> **このファイルは設計ではなく観測の記録である。** 数値は環境に依存するので、
> **測った環境を必ず一緒に書く**（Phase 0 で環境を書き忘れた反省 → [phase0.md](phase0.md)）。

関連: [../roadmap.md](../roadmap.md) Phase 1, [../architecture/audio.md](../architecture/audio.md) §7（SLO）

---

## 測定環境

| | |
|---|---|
| OS | Windows 11 Home 26200 |
| Python | 3.12.11（uv で解決） |
| 測定日 | 2026-08-16 |

---

## 依存の追加とサイズ（R1 の再判定）

**Step C（Provider 基盤）で 16 パッケージが増えた。** STT のためである。

| パッケージ | site-packages 上のサイズ | 何のため |
|---|---|---|
| ctranslate2 | 59.8 MB | faster-whisper の推論エンジン |
| onnxruntime | 38.6 MB | Silero VAD（Phase 1 Step D）/ faster-whisper |
| numpy | 21.5 MB | 音声の共通表現（16 kHz mono float32） |
| tokenizers | 7.5 MB | faster-whisper |
| huggingface_hub | 4.3 MB | faster-whisper（**取得は無効化している** → ADR-023） |
| av | 3.4 MB | faster-whisper |
| faster_whisper | 1.4 MB | 本体 |
| **合計（増分）** | **約 136 MB** | |

### 判定: **R1 は再燃しない。**

R1 の懸念は「**torch 依存だとインストーラ 1-2 GB**」であり、今回の増分はその 1/10 以下である。
CTranslate2 / ONNX を選んだ判断（[ADR-008](../decisions/ADR-008-provider-abstraction.md)）が
そのまま効いている。

### ★ 現時点の配布物サイズには、まだ反映されていない

```
PyInstaller onedir（Step C 時点）: 26.7 MB
```

**増えていない。** 理由は、STT の経路が**まだどこからも import されていない**ため
（`lumi/__main__.py` → greeting / setup / transport のみ）。PyInstaller は
到達しないモジュールを同梱しない。`faster_whisper` の import も遅延させてある。

> **したがってこの 26.7 MB を「依存を足しても増えない」と読んではならない。**
> **Step E（Reactive Loop の配線）の後に測り直す。** 見込みは 26.7 + 約 136 = **160 MB 前後**。

### モデルは含まない

ランタイム（上記）は同梱するが、**STT のモデル（数百 MB）は配布物に含めない**
（ユーザーの明示的な選択に基づく実行時取得 → [ADR-023](../decisions/ADR-023-llm-runtime-and-model-acquisition.md)）。
Silero VAD（数 MB）は同梱する。

---

## レイテンシ SLO

〔Step F で測る。区間別 p50/p95/p99 と `unaccounted_ms`〕

## barge-in レイテンシ

〔Step F で測る。mute latency < 50 ms / 知覚 barge-in latency < 120 ms〕

## LLM モデル選定（未確定事項 #5）

〔Step E 以降。日本語会話品質と Tool Calling 品質を実測する〕
