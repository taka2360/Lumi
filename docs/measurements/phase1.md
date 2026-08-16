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

### 配布物サイズ（Step E で配線が通った後）

| 時点 | PyInstaller onedir | **インストーラ**（NSIS） | 備考 |
|---|---|---|---|
| Phase 0 | 26.7 MB | **13.1 MB** | |
| Step C（Provider 実装） | 26.7 MB | — | **増えない**。STT の経路がどこからも import されていない |
| **Step E（配線後）** | **234.8 MB** | **65.7 MB** | 見込み 160 MB を**大きく超えた** |

インストーラが onedir の 1/3.6 で済むのは NSIS が LZMA 圧縮するため。
**判定に使うのはインストーラの 65.7 MB** である（ユーザーがダウンロードするのはこれ）。

見込み（約 160 MB）を外した原因は、**site-packages のサイズだけを足したこと**である。
PyInstaller は依存の**ネイティブ DLL を別途集める**ので、そこが漏れていた。

| 内訳（`_internal/`） | サイズ | 何のため |
|---|---|---|
| **av.libs** | **62.6 MB** | PyAV が同梱する FFmpeg。**Lumi は使っていない**（下記） |
| ctranslate2 | 58.8 MB | STT の推論エンジン |
| onnxruntime | 34.6 MB | Silero VAD（barge-in） |
| numpy.libs | 20.1 MB | OpenBLAS |
| hf_xet | 9.1 MB | huggingface_hub の転送高速化。**取得は無効化している** |
| tokenizers | 7.2 MB | STT |
| その他 | 42 MB | Python 本体 / OpenSSL / SQLite / PortAudio / sqlite-vec / Content Pack |

### 判定: **R1 は再燃しない。** ただし 5 倍になった事実は残す

R1 の閾値は「1-2 GB で配布不能」であり、**インストーラ 65.7 MB はその 1/20 以下**である。
**torch を避けた判断はそのまま効いている。**

比較のため: torch（CUDA 付き）は単体で 2-3 GB、CPU 版でも 200 MB 前後ある。
**CTranslate2 + ONNX Runtime で 93 MB** に収まっているのがこの選択の成果である。

### 削れる見込みがあるもの〔Phase 1 では手を付けない〕

| 対象 | 削減見込み | 障害 |
|---|---|---|
| av.libs + av | **65 MB** | `faster_whisper/__init__.py` が `decode_audio` を無条件 import する。Lumi は音声を **numpy 配列で渡す**ので FFmpeg は実行時に使われないが、**import は通ってしまう** |
| hf_xet | 9 MB | モデルの実行時取得（ADR-023）を自前の HTTP に置き換えれば huggingface_hub ごと外せる |

どちらも「使っていないものを外す」だけで**機能を落とさない**。
**Phase 1 の完了条件には効かないので、やらない。** R1 が再燃したときの余地として記録する。

### ★ 固めた実行体だけで壊れたもの（2026-08-16）

**onnxruntime が読めず、Silero VAD が丸ごと死んだ。** つまり barge-in が動かない。
`uv run pytest` は全て通り、**開発環境では一切再現しない。**

```
ImportError: DLL load failed while importing onnxruntime_pybind11_state:
ダイナミック リンク ライブラリ (DLL) 初期化ルーチンの実行に失敗しました。
```

原因は **PyInstaller がビルド機の PATH から VC++ ランタイムを拾ったこと**。
このマシンでは JDK の同梱物が先に見つかっていた。

| DLL | 拾われた版 | 出所 | System32 |
|---|---|---|---|
| msvcp140.dll | **14.16**（VS2017） | `C:\Program Files\Microsoft\jdk-11.0.16.101-hotspot\bin` | 14.51 |
| vcruntime140.dll | **14.16** | 同上 | 14.51 |

onnxruntime.dll はもっと新しいものを要求するため、DllMain が失敗する。

**対処**: `lumi-core.spec` の `_pin_vcruntime()` が、収集された VC++ ランタイムの出所を
**System32 に固定する**。PATH を直すのではなくビルド定義で固定するのは、
**配布物がビルド機の状態に左右されること自体が問題**だからである。

**この種のバグは実行するまで分からない。** `--self-check` に Silero VAD / faster-whisper /
Content Pack の項目を足したのはそのためで、**CI で配布物を検査できる唯一の手段**である。

```
> lumi-core.exe --self-check
  ✓ sqlite-vec / ✓ FTS5 / ✓ PortAudio / ✓ Silero VAD / ✓ faster-whisper
  ✓ Content Pack / ✓ TLS
```

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
