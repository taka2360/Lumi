# Lumi

デスクトップに常駐する AI キャラクター。「伺か」の系譜に、ローカル AI スタック（LLM / STT / TTS / Vision / ベクトル記憶）を統合し、**チャットボットではなく「PC という世界に住んでいる AI 生命体」**を作る。

差別化点は3つ。**真の barge-in**（喋っている途中で遮れる）、**記憶**（覚え、忘れ、矛盾を抱える）、**安全な自律**（自分から動くが鬱陶しくなく、危険な操作は必ず人間の判断を経る）。

## 現在の状態

**Phase 2（Memory）は 2a〜2g すべて実装済み。ただし 2b（投機 STT）の実測が未取得で、[roadmap.md](docs/roadmap.md) の完了条件はまだ満たしていない。** 設計は rev.25 まで完了し承認済み。
コードは、すべて `docs/` の設計に従う。**設計に無いことを実装する前に、設計を先に更新する。**

**Phase 3 に進む前に 2b の実測を取る。** 破棄率・`stt_overlap_ms` は**実際に喋らないと出ない**——それが残っている唯一の理由であり、コードの不足ではない。
実測値 → [docs/measurements/phase2.md](docs/measurements/phase2.md) / [docs/measurements/phase1.md](docs/measurements/phase1.md)

## リポジトリ構成

```
Lumi/
├── docs/          設計（唯一の正）。実装より先にここが変わる
├── core/          Lumi Core — Python / asyncio。権威（判断・状態・ポリシー・記憶）
├── shell/         Lumi Shell — Tauri 2 / Rust。OS 特権プリミティブのみ
├── stage/         Stage WebView — React + TS + Zustand。表現のみ
├── extensions/    〔Phase 5 で作る〕out-of-process Capability Extension（別プロセス・任意言語）
└── content/       Content Pack（キャラ・モデル・音声・人格。**コードを含まない**）
```

`core/lumi/` のモジュール構成 → [docs/architecture/core.md](docs/architecture/core.md) §4

## 技術スタック

| 領域 | 採用 |
|---|---|
| Desktop Shell | Tauri 2（`PlatformShell` で抽象化し Electron 退避路を確保） |
| AI Core | Python / asyncio、単一プロセス、**ハブ** |
| 音声 I/O | Core 内（barge-in critical path を Core に閉じる） |
| Memory | SQLite + sqlite-vec + FTS5。埋め込みは **Harrier-OSS-v1 270M（ONNX q4 / 640 次元 / CPU）** |
| LLM | Ollama（Qwen3系 / Gemma3系） |
| STT / VAD | faster-whisper (CTranslate2, int8) / Silero VAD (ONNX, CPU) |
| TTS | AivisSpeech / VOICEVOX（**別プロセス。CUDA があれば GPU、無ければ CPU**） |
| Character | VRM（`@pixiv/three-vrm`）→ Live2D は Phase 9 |
| ライセンス | **Core = MIT。** GPL/AGPL・非OSS を Core に入れない |

**Core は torch に依存しない**（インストーラサイズ R1）。TTS のデバイス方針は
**CUDA があれば GPU、無ければ CPU。設定で CPU を強制でき、実際の配置を状態として公開する**
（[ADR-025](docs/decisions/ADR-025-tts-on-gpu.md)）。

## 実装前に読むもの

**必ず [docs/DESIGN.md](docs/DESIGN.md)（設計憲法）と、変更対象に対応する詳細ドキュメントを読んでから書く。**

| 変更対象 | 読むドキュメント |
|---|---|
| Attention Arbiter / Activity / Job / barge-in | [contracts/state-machines.md](docs/contracts/state-machines.md), [architecture/agent.md](docs/architecture/agent.md) |
| ツール実行・権限 | [contracts/tool-execution.md](docs/contracts/tool-execution.md), [architecture/permission.md](docs/architecture/permission.md), [interfaces/tool.md](docs/interfaces/tool.md) |
| 信頼レベル・プロンプトインジェクション対策 | [contracts/provenance.md](docs/contracts/provenance.md) |
| イベント・Command・Hook | [contracts/event-model.md](docs/contracts/event-model.md) |
| 記憶 | [architecture/memory.md](docs/architecture/memory.md), [interfaces/memory.md](docs/interfaces/memory.md) |
| **永続化・暗号化・保持期間・削除** | [contracts/privacy.md](docs/contracts/privacy.md) |
| 音声・VAD・TTS・SLO | [architecture/audio.md](docs/architecture/audio.md) |
| 自律行動 | [architecture/autonomy.md](docs/architecture/autonomy.md) |
| World / Internal State | [architecture/world-state.md](docs/architecture/world-state.md) |
| Shell / Stage / Widget | [architecture/ui.md](docs/architecture/ui.md), [interfaces/shell.md](docs/interfaces/shell.md) |
| Extension / Provider | [architecture/extension.md](docs/architecture/extension.md), [interfaces/provider.md](docs/interfaces/provider.md) |
| **ライセンス・配布物・クレジット・外部エンジンの取得** | [licensing.md](docs/licensing.md), [ADR-019](docs/decisions/ADR-019-tts-engine-distribution.md) |
| セキュリティ境界 | [contracts/security-boundaries.md](docs/contracts/security-boundaries.md) |
| 何をいつ作るか | [roadmap.md](docs/roadmap.md) |

**「誰が何をしてよいか」で迷ったら [contracts/authority-matrix.md](docs/contracts/authority-matrix.md) を見る。表に ✓ が無いことをしていたら設計違反。**

## 絶対に破ってはいけないもの

**8つの Invariant。** これらは機能ではなく制約であり、実装のどの段階でも破ってはならない。
→ `.claude/rules/00-invariants.md`（常時ロード）/ 全文と根拠は [contracts/invariants.md](docs/contracts/invariants.md)

## セッション開始時の手順

1. **`docs/DESIGN.md` の「rev.」と「実装フェーズ」を確認する。** 自分の記憶より docs が新しい
2. 今どの Phase の作業かを [roadmap.md](docs/roadmap.md) で確認する。**Phase を飛ばさない**
3. 🔴 マークの「着手前に決めること」が未決なら、**先にそれを片付ける**（Phase 2 のプライバシー方針、Phase 4c の Invariant 8 など）
4. 変更対象に対応する詳細ドキュメントを読む
5. main で作業しない。`feat/<phase>-<topic>` / `fix/<topic>` / `docs/<topic>` を切る

## 開発コマンド

| 何を | コマンド | 場所 |
|---|---|---|
| アプリを起動（Shell + Stage） | `pnpm dev` | リポジトリroot |
| インストーラを作る | `pnpm build` | リポジトリroot |
| Stage だけ起動 | `pnpm stage:dev` | リポジトリroot |
| Core のセットアップ | `uv sync` | `core/` |
| Core を単体起動 | `uv run lumi-core` | `core/` |
| Core のテスト | `uv run pytest` | `core/` |
| 生成設定の A/B（LLM を実際に呼ぶ） | `uv run python scripts/llm_profile_eval.py --out ../ab.md` | `core/` |
| Core の lint / format / 型 | `uv run ruff check` / `uv run ruff format` / `uv run mypy` | `core/` |
| Stage のテスト / lint / 型 | `pnpm test` / `pnpm lint` / `pnpm typecheck` | `stage/` |
| Shell のテスト / lint / format | `cargo test` / `cargo clippy --all-targets -- -D warnings` / `cargo fmt` | `shell/src-tauri/` |

**必要なもの**: Rust（MSVC ツールチェイン）/ Node 24+ / pnpm 11 / uv。Python 3.12 は uv が取得する。

**静的検査**（[authority-matrix.md](docs/contracts/authority-matrix.md) の22項目）— **14項目が実装済み**
（#1 #2 #3 #7 #8 #9 #11 #12 #15 #20 #21 #22 → `core/tests/test_kernel_boundaries.py` /
項目16 → `core/tests/test_audio_vad.py` / **#4 → `stage/src/platform/boundaries.test.ts`**）。
残り8項目は未実装。#10 は Phase 3 と同時。#20〜22 は [ADR-045](docs/decisions/ADR-045-core-module-layering.md) で追加された。

## 進め方の原則

- **各 Phase は単体で使える製品であること。** Phase 1 で止めても「喋るデスクトップキャラ」として成立する
- **後から挿入するコストが高いものだけ、先に作る。** Kernel・型・契約がこれに当たる
- **判断は決定論的コードで、生成は LLM で。** 「動くべきか」を LLM に決めさせない
- **LLM を呼ばずにテストできること。** 呼ばないとテストできないなら設計が間違っている
- **黙って劣化しない。** 動かないものは明示的に失敗させる
- **迷ったら安全側（fail-closed）に倒す**
