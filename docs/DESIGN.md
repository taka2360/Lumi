# Lumi — 設計憲法 (DESIGN.md)

> **このドキュメントの位置づけ**
> `DESIGN.md` はプロジェクト全体の「憲法」である。ここには全体を理解するために必要な情報だけを置き、個別コンポーネントの詳細仕様は `architecture/` `contracts/` `interfaces/` に置く。
>
> **実装前にこのファイルと、変更対象に対応する詳細ドキュメントを読むこと。**
> 実装によって設計が変わった場合は、コードだけを変更せず `decisions/` に ADR を追加してから該当ドキュメントを更新すること。

| | |
|---|---|
| Status | **承認済み（2026-08-15）** |
| Revision | rev.16 |
| 実装フェーズ | **Phase 1（MVP: Talking Desktop Character）完了。Phase 2（Memory）着手前の 🔴 2件を決着。** → [roadmap.md](roadmap.md) |

> **rev.16 の変更点**（Phase 1 を閉じ、Phase 2 の 🔴 2件を決着させた）
> 1. **`contracts/privacy.md` を新設した** → [ADR-038](decisions/ADR-038-privacy-and-data-retention.md)。
>    記憶 DB は保存時に暗号化する（ランダム鍵を DPAPI に預ける。**ユーザーはパスワードを管理しない**）。
>    保持期間は既定で期限あり・設定で無期限も選べる。**「忘れる」と「消える」を混ぜない**——
>    記憶レコードは decay/archive の対象であって、保持期間の対象ではない
> 2. **投機 STT を導入した** → [ADR-039](decisions/ADR-039-speculative-stt.md)。
>    VAD の無音待ち 0.43 s に STT 0.22 s を重ね、クリティカルパスを 1.27 → 1.10 s（p50 目標の 73%）にした。
>    **記憶検索 0.05 s を足しても 85% 規則を破らない。目標は動かしていない**
> 3. 予算表を「直列区間の表」から**クリティカルパスの表**に変えた。
>    `critical_path_ms` を新設し、`unaccounted_ms` の定義を `total_ms - critical_path_ms` にした
> 4. **DomainEvent の保持ポリシーが決着した**（既定 30 日 / 全消去の対象）。Phase 3 まで持ち越さない

> **rev.14 の変更点**（Ollamaの通常インストール経路を、未起動と誤判定しない）
> 1. 実行ファイル初検出後、APIが立つまで15秒の起動猶予を設けた → [ADR-037](decisions/ADR-037-consented-ollama-model-pull.md)。
> 2. Ollama本体は引き続き検出のみだが、推論モデルは対象・概算サイズを明示して同意を得た後、
>    ローカル `/api/pull` から取得できるようにした。

> **rev.15 の変更点**（モデル選択時の既存ローカルモデルと操作配置）
> 1. 「別のモデルを選ぶ」では Ollama の `/api/tags` から既存モデルも表示し、
>    既存モデルは追加取得なしの `select` として選べるようにした → [ADR-037](decisions/ADR-037-consented-ollama-model-pull.md)。
> 2. ダウンロード確認中のインスペクター・設定・アプリ操作は、確認カードの上段に置く。

> **rev.13 の変更点**（STT のウォーム失敗を状態として表したら、導入状態と実行状態の混同が露出した）
> 1. **STT にも runtime 軸を追加した** → [ADR-035](decisions/ADR-035-separate-stt-installation-and-runtime-state.md)。
>    `state: failed` はモデル取得失敗のまま維持し、CTranslate2 のロード失敗は
>    `state: installed × runtime: failed` として表す。**入っていることと動くことを混ぜない**
> 2. STT が「使える」の定義を `installed × runtime: ready` に変更した。
>    ウォームは `starting → ready / failed` を報告し、失敗時は `boot: blocked` のまま音声入力を開かない
>
> **rev.12 の変更点**（実際に起動してみて、Phase 0 からの前提が1つ崩れた）
> 1. **会話に必要な3つ（STT / LLM / TTS）が揃うまでキャラクターを出さない** → [ADR-034](decisions/ADR-034-gate-startup-on-complete-setup.md)。
>    起動フェーズに `blocked` を足し、不足があればセットアップ画面（**不足項目 + 解決方法 + 終了**）を出す。
>    **「キャラクターを人質にしない」を取り消した** — 「立っているのに反応しない = 壊れて見える」は
>    `starting` の十数秒より**永久に返事が来ない状態にこそ強く当てはまる**
> 2. **取得の失敗をセットアップ完了として扱わない。** 失敗したら「再試行 / 今は取得しない」を出す
>    （再試行の上限は無し。**押すのは常にユーザー**であり催促ではない）
> 3. **「答えたか」を永続化しなくなった**（`setup.json` は廃止）。必須になった以上、
>    答えを覚えることは「不足したまま二度と聞かない」と同義であり、**出口の無い状態**を作る
> 4. **Stage から Lumi を終了できるようにした**（`shell_app_quit`）→ [interfaces/shell.md](interfaces/shell.md)。
>    セットアップで止まっているユーザーは、**トレイに Lumi が居ることをまだ知らない**

> **rev.11 の変更点**（Step G。Stage 側を作ったら、設計の穴が2つ出た）
> 1. **Stage → Core の要求方向を作った**（`request`）→ [ADR-028](decisions/ADR-028-stage-initiated-request.md)。
>    設定 UI が**原理的に作れなかった**（クライアントからは `hello` と `result` しか受理していなかった）。
>    「経路が無いことで決定の起点が Core であることを保証する」という Phase 0 の記述を撤回する。
>    **守りたいのは Invariant 1 であって、経路の不在はその手段でしかない。**
>    **登録した method しか届かない**（fail-closed）
> 2. **設定の保存形式を決めた** → [architecture/core.md](architecture/core.md) §6b（未確定事項 #9 の決着）。
>    **JSON / `<data_dir>/settings.json`**。壊れたファイルは**絶対に上書きしない**（ユーザーの意図の唯一の写しを壊す）
> 3. **`vad_ms` の予算を実測に合わせた** → [architecture/audio.md](architecture/audio.md) §7。
>    予算 0.18 s と `min_silence_duration_ms` 400 ms は**同じ待ち時間を2箇所に別の数値で書いていた**。
>    **`vad_ms` は実装のコストではなくターンテイキングの方針**であり、最適化で消えない。
>    帰結として区間合計が p50 目標を超えた（106%）→ 未確定事項 8b
> 4. **Inspector と吹き出しは `stage` ウィンドウの中に置く** → [architecture/ui.md](architecture/ui.md) §5。
>    `WsServer` は **role ごとに接続を1本しか持たない**ので、別ウィンドウはキャラクターの接続を奪う
>
> **rev.10 の変更点**（音声入力の精度を実測で追い込んだ。**3つとも「測って初めて分かった」もの**）
> 1. **サンプルレート変換を自前の polyphase FIR にした** → [ADR-026](decisions/ADR-026-polyphase-resampler.md)。
>    3-tap 移動平均は **8 kHz 上の成分を −6 dB しか落とさず音声帯域へ折り返していた**。
>    日本語の摩擦音（し / す / つ）の鏡像が子音識別帯域に重なっており、**STT 精度が悪かった主因**。
>    加えて VAD スレッドが 32 ms ごとに純関数を呼んでいたため、毎秒 31 回フィルタが再スタートしていた
> 2. **VAD のプリロールを 80 → 400 ms にした** → [architecture/audio.md](architecture/audio.md) §5。
>    **Silero の確率は無声子音では上がらない**ので、「ちょっと」「さっき」「机」のような語は
>    閾値を超える時点で語頭が区間の外にあった。**落ちた語は全部無声子音で始まっていた**
> 3. **STT の既定モデルを `small` → `large-v3-turbo` にした** → [ADR-027](decisions/ADR-027-stt-model-large-v3-turbo.md)。
>    1 と 2 で語頭欠落は消えたが、**残った誤りが語彙の取り違えでパラメータでは消えなかった**。
>    CER 7.2% → 3.6%。§7 の VRAM 実測を 0.4 → 1.0-1.2 GB に更新（**合計 6.9 → 7.5 / 12 GB**）
> 4. **ユーザーの発話も吹き出しに出すことにした**（`stage.user.said`）→ [interfaces/renderer.md](interfaces/renderer.md)。
>    **「聞き違えた」と「聞こえていなかった」は外から見ると区別がつかない**

> **rev.9 の変更点**（Phase 1 の実測が設計を1つ覆した）
> 1. **TTS に GPU を使えるようにした** → [ADR-025](decisions/ADR-025-tts-on-gpu.md)。
>    ADR-008 の「TTS は CPU で VRAM を一切消費しない」を**実測により撤回**した。
>    CPU の TTS は 24 コアを飽和させて 0.9 秒かかり、**p95 2.0 秒の完了条件に届かない**。
>    GPU に 1.0 GB 置くと 440 ms になる。**守ろうとしていた VRAM の余裕は、そもそも使われていなかった**
> 2. **§7 の VRAM 表に実測値を入れた**（LLM 5.5 / STT 0.4 / TTS 1.0 = 6.9 GB / 12 GB）
> 3. **プロンプト組み立ての予算と切り落とし順序を定義した** → [architecture/agent.md](architecture/agent.md) §3（§12 に行を追加）

> **rev.8 の変更点**（Phase 0 のクローズと、Phase 1 着手前に決めたこと）
> 1. **Phase 0 の完了条件を達成した。** 別マシンのインストーラから起動し、キャラクターが立って一言喋るところまで確認した。
>    **セットアップ周りの検証手順 15〜18 は未実施**（roadmap に残す）。
>    **既定同梱 VRM モデルが決まった**（再配布 OK / 改変 OK / クレジット不要 / VRM 0.0）→ [licensing.md](licensing.md) §4.5。未確認 #5 が解消
> 2. **LLM ランタイムと推論モデルの取得方針を決めた** → [ADR-023](decisions/ADR-023-llm-runtime-and-model-acquisition.md)。
>    **Ollama は検出のみ**（取得もインストールもしない。ロールバックが保証できないため）/ **Silero VAD は同梱**（barge-in は中核機能で「取得するまで遮れない」を作らない）/ **STT モデルは同意に基づく実行時取得**。
>    **ライブラリ既定の自動ダウンロードを無効化する**（Network-optional がライブラリの都合で迂回されるため）。roadmap 未確定事項 #3 が解消
> 3. **Activity の priority 体系を決めた** → [ADR-024](decisions/ADR-024-activity-priority.md)。
>    `interruptible_by: set[int]` を **`interruptible_at: int` の単一閾値**に置き換え、判定を `p.priority >= cur.interruptible_at` にした。
>    **`>` ではなく `>=` なのは、barge-in が同一 priority の preempt だから。** roadmap 未確定事項 #10 が解消
> 4. **`provenance.py` をトップレベルの依存ゼロモジュールにした** → [architecture/core.md](architecture/core.md) §4。
>    `Signal` が `trust_level` を持つため、memory/ の下に置くと **kernel → memory の逆依存**が生まれる

> **rev.7 の変更点**（Phase 0 の実装と実測による修正。**すべて「作ってみたら想定と違った」もの**）
> 1. **リップシンクの生成方式を確定した。** 口の形はモーラ列から、時間は**合成された音声の長さ**から割り振る。
>    AivisSpeech は音素長を返さない（全モーラ `0.0`）ため、当初の「TTS 出力から音素タイミングを取る」は成立しなかった → [interfaces/renderer.md](interfaces/renderer.md)
> 2. **duplex stream を使わないことにした。** duplex が開ける条件はユーザーの機材が決めてしまい、
>    分離ストリームでもドリフトは測定分解能以下だった。reference signal は Core が自前で持つ → [ADR-020](decisions/ADR-020-split-audio-streams.md)。roadmap 未確定事項 #4 が解消
> 3. **音声デバイスの選択方針を確定した**（OS の既定デバイス / WASAPI / デバイス既定レート / **開通は最初のフレームの到着で判定**）→ [architecture/audio.md](architecture/audio.md) §8
> 4. **TTS エンジンプロセスの所有者を Core にした。** Lumi が起動したものだけ Lumi が止める → [architecture/core.md](architecture/core.md) §6
> 5. **使用中モデルのライセンス全文は、モデル自身の manifest が持っている**ことが分かった（licensing.md 未確認 #2 が解消）→ [licensing.md](licensing.md) §4.4
> 6. **Python サイドカーを PyInstaller の onedir で固め、`resources` として同梱する**ことを決めた。
>    onefile は**強制終了のたびに `%TEMP%` へ 21 MB の残骸を残す** → [ADR-021](decisions/ADR-021-sidecar-packaging.md)。roadmap 未確定事項 #2 が解消
> 7. **サードパーティ通知を依存グラフから生成するようにした**（284 件）。
>    **GPL / AGPL / 非 OSS を見つけたらビルドを失敗させる** → [licensing.md](licensing.md) §6

> **rev.6 の変更点**（Phase 0 の 🔴 ブロッカー解消）
> 1. **音声ライブラリ・TTS エンジンの利用規約を調査し、[licensing.md](licensing.md) に記録した。** roadmap 未確定事項 #1 が解消 → §10
> 2. **Lumi は公開配布される前提で設計する**ことを確定した。これにより再配布・クレジット・ACML 特例の各義務が発生する
> 3. **TTS エンジンを配布物に含めない**ことを決定。ユーザーの明示的な選択に基づく実行時取得とした → [ADR-019](decisions/ADR-019-tts-engine-distribution.md)
> 4. **クレジット表示を Phase 0 の必須項目にした**（配布物ができた時点で義務が発生し、遡って直せないため）
> 5. **`Provider.attribution()` を Phase 1 の interface に追加した** → [interfaces/provider.md](interfaces/provider.md)
> 6. **実在の人物を演じる機能を非目標に追加した**（ACML 禁止事項1 を構造的に回避する）→ §2

> **rev.5 の変更点**（レビュー指摘への対応）
> 1. **out-of-process では `BindVerifier` が成立しない**ことを認め、Tool を Class A / Class B に分けた。`fs` / `computer` は in-core に移した → [ADR-017](decisions/ADR-017-out-of-process-tool-contract.md)
> 2. **Policy を `decide()` 関数の単一定義にした。** 「自律は1段上がる」は L3 で表と矛盾していたため撤回 → [architecture/permission.md](architecture/permission.md)
> 3. **会話履歴の trust を定義した**（`block` / `history` / `session` の3スコープ、`session_trust` は sticky）→ [contracts/provenance.md](contracts/provenance.md)
> 4. **`foreground` を単一参照として定義し、`Job` を第一級概念にした** → [ADR-018](decisions/ADR-018-foreground-and-jobs.md)
> 5. **オーディオコールバック内で VAD 推論を回さない**構造に変更 → [architecture/audio.md](architecture/audio.md)
> 6. **レイテンシ予算に予備枠を明示した**（区間合計は p50 目標の 85% 以下）
> 7. **Phase 4 を 4a / 4b / 4c に分割した** → [roadmap.md](roadmap.md)
> 8. **重複記述を SSoT + リンクに整理した** → §12

---

## 目次

- [1. 目的とビジョン](#1-目的とビジョン)
- [2. 非目標](#2-非目標)
- [3. 設計原則](#3-設計原則)
- [4. 不変条件（Invariants）](#4-不変条件invariants)
- [5. 全体アーキテクチャ](#5-全体アーキテクチャ)
- [6. 主要コンポーネント](#6-主要コンポーネント)
- [7. 技術選定](#7-技術選定)
- [8. 設計の確定度レベル](#8-設計の確定度レベル)
- [9. MVP と将来の拡張方針](#9-mvp-と将来の拡張方針)
- [10. AIRI との関係](#10-airi-との関係)
- [11. ドキュメント索引](#11-ドキュメント索引)
- [12. 単一定義（SSoT）の規則](#12-単一定義ssotの規則)

---

## 1. 目的とビジョン

「伺か」の系譜にある常駐デスクトップキャラクターに、現代のローカルAIスタック（LLM / STT / TTS / Vision / ベクトル記憶）を統合し、**チャットボットではなく「PCという世界に住んでいるAI生命体」**という体験を作る。

達成すべきこと:

1. デスクトップに常駐し、話しかければ答え、**こちらを遮ってでも聞く**（真の barge-in）
2. 過去を覚えていて、忘れ、思い出し、矛盾を抱えられる
3. PCで今何が起きているかを知っている（World Model）
4. 自分から動くが、**鬱陶しくない**
5. 危険な操作は必ず人間の判断を経る
6. 将来クラウドLLM・高度なゲームAI・第三者Extensionへ拡張できる

### 「ローカル完結」の定義

| | 定義 | Lumi |
|---|---|---|
| Air-gapped | ネットワークを一切使わない | ✗ |
| Local-first | 中核はローカル、外部は補助 | — |
| **Network-optional** | 外部通信は任意・明示的 | **✓** |

> **推論・状態・判断（inference / state / decision）はローカルで完結する。外部ネットワークの利用は、Capability として明示的に許可された場合にのみ発生する。**

これにより Browser Extension や Web検索は「中核がクラウド化した」のではなく「ネットワークという能力を明示的に与えた」と一貫して説明できる。クラウドLLMも「LLMProvider をネットワーク能力を持つものに差し替えた」という同じ枠組みで扱う。

---

## 2. 非目標

現段階では作らない。これらを作らないことが、作るものの品質を守る。

- クラウドサービス／マルチユーザー／アカウント／課金
- Web版・モバイル版（デスクトップ単一形態に集中）
- 完全自律・無人運用（人間が居る前提の同居エージェント）
- AIRI のフォーク／コード流用
- 汎用エージェントフレームワーク化（Lumi という製品のための設計）
- 学習・ファインチューニング基盤
- **実在の人物を演じる機能**（音声・人格ともに）。Lumi は Lumi として振る舞う 〔rev.6 追加〕

> 最後の1つは他と性質が違う。**能力の不足ではなく、意図的な制約**である。音声モデルのライセンス（ACML 禁止事項1: なりすまし・誤解を招く利用）を、機能を作らないことで構造的に回避する → [licensing.md](licensing.md) §5

---

## 3. 設計原則

1. **LLM は「脳」であって OS ではない。** LLM は提案し、Core が決める
2. **Core は権威を持つが、能力の実装を持たない。** → [architecture/core.md](architecture/core.md)
3. **判断は決定論的コードで、生成は LLM で。** 「動くべきか」は LLM に決めさせない
4. **常時 LLM 推論しない。** 高頻度処理と LLM 推論を階層で分離する
5. **危険な操作には Permission を要求する。** 例外経路を作らない
6. **交換したいもの・追加したいものだけを Extension にする。** Core を過剰に plugin 化しない
7. **抽象化は「実際に必要になる可能性 × 変更コスト」で判断する。** 「将来使うかもしれない」だけでは抽象層を足さない
8. **後から挿入するコストが高いものだけ、先に作る。** Kernel・型・契約がこれに当たる

---

## 4. 不変条件（Invariants）

**これらは機能ではなく制約である。実装のどの段階でも破ってはならない。**
詳細と根拠 → [contracts/invariants.md](contracts/invariants.md)

| # | 名前 | 内容 |
|---|---|---|
| **1** | Authority | LLM・Stage・Shell・Extension は権限の最終決定者ではない。最終判断権は **Core Kernel にのみ**存在する（唯一の例外は Invariant 8） |
| **2** | Tool Gate | 副作用を持つ操作は、例外なく Permission Kernel を経由する。バイパス経路を実装してはならない |
| **3** | Untrusted Data | 外部から取得されたテキスト・画像・ファイル・Web内容・ゲーム画面は、明示的に trusted 化されない限り、**命令ではなくデータ**として扱う |
| **4** | Attention | 同時に **foreground** である Activity は**常にちょうど1つ**（`_foreground` 参照が唯一の所有者）。ユーザー入力による **Activity の中断**は例外なく Attention Arbiter を経由する（**再生バッファのミュートは Activity 状態遷移ではないため対象外**） |
| **5** | Capability | Extension は manifest に宣言した能力を超えて実行できない。実効権限は `manifest ceiling ∩ policy ∩ user grant` の交差 |
| **6** | No Hidden Authority | Stage・Extension・Provider・Tool・LLM のいずれも、Core が認識・監査できない状態変更や副作用を起こしてはならない |
| **7** | No Laundering | いかなる自動処理も TrustLevel を下げられない。`tainted → trusted` の昇格は**人間の明示的確認を経た場合のみ** |
| **8** | Unautomatable Consent | Lumi の権限確認 UI は Lumi 自身が操作できない。Shell は保護対象ウィンドウへの `os.input.*` / `os.capture.*` を**無条件に拒否する** |

---

## 5. 全体アーキテクチャ

### 5.1 Core の定義

> **Core は権威を持つが、能力の実装を持たない。**

```
Core が持つもの（権威）              Core が持たないもの（能力）
──────────────────────              ──────────────────────────
Decision     何をすべきか            Browser
State        今どうなっているか       Computer / Input injection
Policy       何が許されるか           Filesystem
Scheduling   いつやるか              Game
Coordination 誰がやるか              Sensor
Memory       何を覚えているか         Vision model
```

判定基準: **「これを外しても Lumi は Lumi か？」**
ブラウザを外しても Lumi だが、記憶や Attention Arbiter を外すと Lumi ではない。

### 5.2 プロセス構成

```
┌──────────────────────── Lumi Shell (Tauri 2 / Rust) ─────────────────────────┐
│  責務: OS特権プリミティブのみ。判断を持たない（Invariant 8 の拒否を除く）        │
│  透過/最前面/クリックスルー/ヒットテスト・トレイ・ホットキー                     │
│  スクリーンキャプチャ・入力インジェクション・Coreサイドカーの起動と生存監視       │
│  Core からの os.* 要求を認証・schema検証・allowlist検査してから実行 (B3)        │
│                                                                              │
│   ┌────────── Stage WebView (React + TS + Zustand) ──────────┐               │
│   │  責務: 表現のみ。ビジネスロジックを持たない                │ ← Tauri IPC   │
│   │  VRM描画・表情/モーション/リップシンク・吹き出し            │   shell.*     │
│   │  Widgetホスト(sandboxed iframe) + Widget Broker・設定UI    │               │
│   │  権限プロンプト（独立ウィンドウ / Invariant 8 の保護対象）  │               │
│   └──────────────────────────────────────────────────────────┘               │
└──────────────────────────────────────────────────────────────────────────────┘
              │ os.* (WS)                               │ stage.* (WS)
              ▼                                         ▼
┌────────────────────── Lumi Core (Python / asyncio) ──────────────────────────┐
│  ┌── Attention Arbiter ──┐  単一の「今なにをしているか」の所有者               │
│  └───┬───────────┬───────┘                                                    │
│  ┌───▼──────┐ ┌──▼─────────────┐                                             │
│  │ Reactive │ │ Deliberative   │                                             │
│  │ Loop     │ │ Loop (Drives)  │                                             │
│  └──────────┘ └────────────────┘                                             │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐ ┌──────────┐ ┌─────────┐ │
│  │ Memory  │ │ World    │ │Internal│ │ Permission│ │ Tool     │ │ Event   │ │
│  │ System  │ │ State    │ │ State  │ │ Kernel    │ │ Registry │ │ Bus     │ │
│  └─────────┘ └──────────┘ └────────┘ └───────────┘ └──────────┘ └─────────┘ │
│  ┌── Audio I/O (capture / VAD / playback / EchoGuard) ──┐ barge-in critical   │
│  └──────────────────────────────────────────────────────┘ path をここに閉じる │
│  ┌── Extension Host ──┐  ┌── Provider Registry (LLM/STT/TTS/Embed/Vision) ──┐│
│  └─────────┬──────────┘  └──────────────────────────────────────────────────┘│
└────────────┼──────────────────────────────────────────────────────────────────┘
             │ ext.* (WS / stdio, capability-gated)
   ┌─────────┼─────────┬──────────────┐
   ▼                   ▼              ▼
 Sensor Ext      Browser Ext     GameAgent Ext ...

 外部エンジン（別プロセス / 所有しない）: Ollama │ AivisSpeech / VOICEVOX
```

**Core がハブである。** Shell も Stage も Extension も Core のクライアント。

### 5.3 通信の namespace

| namespace | 経路 | 内容 | 例 |
|---|---|---|---|
| `shell.*` | Tauri IPC (Shell ↔ Stage) | ウィンドウ自身の見た目と入力。**1ms以下であるべきもの** | `shell.window.set_clickthrough`, `shell.hover.state` |
| `stage.*` | WS (Core → Stage) | Lumi の表現と状態 | `stage.character.speak`, `stage.widget.open` |
| `os.*` | WS (Core → Shell) | OS特権操作の依頼 | `os.capture.screenshot`, `os.input.click` |
| `ext.*` | WS/stdio (Core ↔ Extension) | Tool 呼び出し、Sensor push | `ext.tool.invoke`, `ext.sensor.push` |

> **規則: `shell.*` は絶対に AI の判断を運ばない。`stage.*` は絶対に OS 特権を要求しない。**

### 5.4 データフロー（会話）

```
[audio callback]  capture → ring buffer / mute_flag が立っていれば出力を無音化
       │                    推論しない。確保しない。ロックしない
       ▼
[VAD スレッド]  Silero VAD (ONNX, CPU)
       ├─(mute閾値を超えた)──→ playback.mute_flag.set()   ★ここで音が止まる。同期。
       │                          Arbiter を経由しない（Activity 状態遷移ではない）
       ├─(発話区間の開始を確定)→ asyncio へ
       └─(発話区間の終了)─────→ asyncio へ（音声つき）
                                     │
       ┌─────────────────────────────┘
       ▼
[asyncio]
   speech-start確定 → AttentionArbiter.interrupt()  ← Activity を止めるのはここ
   speech-end      → STT → Activity提案(conversation, user_initiated)
                        → MemoryRetrieval(予算内)
                        → PromptAssembly(persona + world投影 + internal state
                                        + memory + turns + ContextBlock[provenance])
                        → LLM stream
                            ├─ text  → 文分割 → TTS → 再生 → リップシンク
                            ├─ <|ACT|> → ExpressionIntent → Stage
                            └─ tool call → Kernel実行契約 → 結果を untrusted で再投入
                        → EpisodeRecord（記憶化はまだしない）
```

> **「音が止まる」と「Activity が止まる」は別の経路である。** ユーザーが体感するのは前者であり、それは asyncio を経由しない。

詳細 → [architecture/agent.md](architecture/agent.md), [architecture/audio.md](architecture/audio.md)

### 5.5 データフロー（自律）

```
tick(30s, LLM呼び出しなし)
  → Drive更新(World + Internal + Memory signals)
  → effective_drive = base × fatigue × quiet × budget
  → argmax > threshold ?  ── No → 終了（LLMを呼ばない）
        │ Yes
  → AutonomyGate（在席/DND/cooldown/budget/quiet hours/permission）── 不通過 → penalize
        │ 通過
  → AttentionArbiter.propose(autonomous, self_initiated)
        │ Accepted（会話中なら Deferred）
  → ここで初めて LLM が「具体的に何をするか」を生成
```

詳細 → [architecture/autonomy.md](architecture/autonomy.md)

---

## 6. 主要コンポーネント

| コンポーネント | 責務 | 詳細 |
|---|---|---|
| **Attention Arbiter** | 「今なにをしているか」の単一の所有者。foreground Activity は常に1つ。Activity 中断の唯一の入口。**推論資源の調停者**（`inference_lease`） | [agent.md](architecture/agent.md) |
| **Reactive Loop** | 会話。低レイテンシ。イベント駆動 | [agent.md](architecture/agent.md) |
| **Deliberative Loop** | 自律。tick駆動。Drive System で発火判定 | [autonomy.md](architecture/autonomy.md) |
| **Job** | foreground を取らない背景処理（Reflection / 再埋め込み）。`actor = system` 固定、推論は lease 制 | [agent.md](architecture/agent.md), [ADR-018](decisions/ADR-018-foreground-and-jobs.md) |
| **Memory System** | Working / Episodic / Semantic / Procedural。形成・忘却・矛盾・検索 | [memory.md](architecture/memory.md) |
| **World State** | 外界の観測。Sensor が push。TTL で失効する | [world-state.md](architecture/world-state.md) |
| **Internal State** | Lumi 自身の状態（mood / fatigue / drives）。失効しない | [world-state.md](architecture/world-state.md) |
| **Permission Kernel** | 権限判断の唯一の所有者。Scope 正規化・Grant・監査 | [permission.md](architecture/permission.md) |
| **Tool Registry** | Tool の登録と実行統括。Kernel実行契約を強制する | [tool-execution.md](contracts/tool-execution.md) |
| **Event Bus** | DomainEvent の採番と配送。per-stream ordering | [event-model.md](contracts/event-model.md) |
| **Audio I/O** | mic / VAD / playback / EchoGuard。barge-in critical path | [audio.md](architecture/audio.md) |
| **Extension Host** | Provider（in-core）と Capability（out-of-process）の2機構 | [extension.md](architecture/extension.md) |
| **Shell** | OS特権プリミティブ。判断を持たない。B3 の検証層 | [ui.md](architecture/ui.md) |
| **Stage** | 表現のみ。VRM描画・Widget Broker | [ui.md](architecture/ui.md) |

### 依存関係の向き

```
Stage ──→ Core ←── Shell
              ↑
         Extension

Core内部:
  AttentionArbiter ──→ (Reactive | Deliberative)
  Reactive/Deliberative ──→ Memory, World, Internal, Provider, ToolRegistry
  ToolRegistry ──→ PermissionKernel ──→ (Canonicalizer, Policy, Grant, Audit)
  すべて ──→ EventBus (発行のみ)
```

**逆向きの依存を作らない。** PermissionKernel は Tool を知らない。Memory は Agent を知らない。EventBus は誰も知らない。

---

## 7. 技術選定

| 領域 | 決定 | 理由 | ADR |
|---|---|---|---|
| Desktop Shell | **Tauri 2** + `PlatformShell` 抽象 | メモリ数十MB級。RAM/VRAM をローカルAIに全振りできる。Electron 退避路を確保 | [ADR-001](decisions/ADR-001-desktop-shell-tauri.md) |
| AI Core | **Python / asyncio**、単一プロセス、ハブ | VAD/STT/Embedding/TTS の生態系が Python に集中 | [ADR-002](decisions/ADR-002-python-core-as-hub.md) |
| 音声 I/O | **Core 内**（AIRI はブラウザ側） | barge-in critical path を Core 内に閉じる | [ADR-003](decisions/ADR-003-audio-in-core.md) |
| Memory | **SQLite + sqlite-vec + FTS5** | 単一ファイル・追加プロセスゼロ。`VectorStore` で隔離 | [ADR-004](decisions/ADR-004-sqlite-vec-memory.md) |
| Character | **VRM 優先** → Live2D 後追い | `@pixiv/three-vrm` が MIT。非OSS物がリポジトリにもバイナリにも混ざらない | [ADR-009](decisions/ADR-009-renderer-intent-based.md) |
| LLM | **Ollama**（Qwen3系 / Gemma3系）→ `LLMProvider` で交換可能 | ローカル無料優先。将来クラウドへ | [ADR-008](decisions/ADR-008-provider-abstraction.md) |
| STT | faster-whisper (CTranslate2, int8) | torch非依存でインストーラを小さく保てる | [ADR-008](decisions/ADR-008-provider-abstraction.md) |
| VAD | Silero VAD (ONNX Runtime, CPU) | 軽量。VRAM を使わない | [ADR-008](decisions/ADR-008-provider-abstraction.md) |
| TTS | **AivisSpeech**（第一）/ VOICEVOX（代替）/ Kokoro（英語） | 日本語品質・別プロセス・ライセンス境界。**GPU があれば使う**（CPU では約 2 倍遅い → ADR-025） | [ADR-008](decisions/ADR-008-provider-abstraction.md), [ADR-025](decisions/ADR-025-tts-on-gpu.md) |
| Embedding | Ruri v3系 / bge-m3（ONNX, CPU） | 日本語検索品質。VRAM を使わない | [ADR-008](decisions/ADR-008-provider-abstraction.md) |
| Browser | Playwright（out-of-process Extension） | Apache-2.0 | — |
| ライセンス | **Core = MIT** | GPL/AGPL・非OSS を Core に入れない。Live2D / TTSエンジン / 音声モデル / 商用SDK は Extension・外部プロセス境界に隔離 | — |

### GPU / VRAM 戦略（RTX 4070 12GB、実効約10.8GB）

| モデル | 配置 | VRAM（見積） | **Phase 1 実測** |
|---|---|---|---|
| LLM (Qwen3.5 9B Q4_K_M) | GPU / pinned | ~6.5 GB | **5.5 GB** |
| STT (faster-whisper int8) | GPU（空きがあれば）/ CPU | ~1.0 GB | **1.0-1.2 GB**〔ADR-027〕 |
| VAD (Silero ONNX) | **CPU固定** | 0 | 0 |
| Embedding (ONNX) | **CPU固定** | 0 | — |
| **TTS (AivisSpeech/VOICEVOX)** | **別プロセス。GPU があれば使う**〔ADR-025〕 | ~1.0 GB | **1.0 GB** |
| Vision (Phase 5) | オンデマンド、使用後アンロード | ~3-4 GB | — |

**合計 7.5 / 12 GB**〔2026-08-17 実測 → [measurements/phase1.md](measurements/phase1.md)〕。
STT を `small`（0.4 GB）から `large-v3-turbo` に変えたぶん増えた（[ADR-027](decisions/ADR-027-stt-model-large-v3-turbo.md)）。
**見積 ~1.0 GB の枠に対して実測が 0.4 GB だっただけで、枠を広げたわけではない。**

**当初は「TTS を CPU に置いて LLM に VRAM を全振りする」を TTS 選定の主因としていたが、
Phase 1 の実測で撤回した**（[ADR-025](decisions/ADR-025-tts-on-gpu.md)）。
CPU の TTS は 24 コアを飽和させて 0.9 秒かかり、**p95 2.0 秒の完了条件に届かない**。
GPU に 1.0 GB 置くと 440 ms になる。**守ろうとしていた VRAM の余裕は、そもそも使われていなかった。**

**GPU が無い環境では CPU で動く。** ただし SLO は GPU 構成での約束である。

`ModelResourceManager` の実装は Phase 5。Phase 1 では `Provider.load/unload/resource_hint()` の**窓口だけ**を確定させる（後から Provider にライフサイクルを追加すると全 Provider の書き換えになるため）。

---

## 8. 設計の確定度レベル

**この設計書のすべてが同じ重みではない。**

| レベル | 意味 | 変更の扱い |
|---|---|---|
| **Confirmed** | 原則。変わるならプロジェクトの前提が変わる | ADR を新規作成し、影響範囲を洗ってから変更 |
| **Provisional** | 現時点の最良の判断。実測で調整される前提 | 通常の設計変更として記録・更新 |
| **Deferred** | 方向性のみ確定。詳細は該当 Phase で設計 | この段階では決めない |

| 項目 | 確定度 |
|---|---|
| 8つの Invariant | **Confirmed** |
| `contracts/` の全内容（Authority Matrix / Security Boundary / Provenance / 状態機械 / Event model / Kernel実行契約 / **Privacy**） | **Confirmed** |
| Core ハブ + namespace 分離 | **Confirmed** |
| World State / Internal State / Memory の三分離 | **Confirmed** |
| Extension 2機構 + `in-core ⟹ official` 制約 | **Confirmed** |
| **Tool の Class A / Class B 分離**（副作用 lane は in-core） | **Confirmed** |
| **Policy が `decide()` の単一定義であること** | **Confirmed** |
| **trust の3スコープと `session_trust` の sticky 性** | **Confirmed** |
| **`foreground` の定義と `Job` の分離** | **Confirmed** |
| 音声 I/O を Core 側に置く | **Confirmed** |
| **オーディオコールバックの3層分離** | **Confirmed** |
| Memory 4層 + provenance + assertion_mode | **Confirmed** |
| Drive System の存在と決定論性 | **Confirmed** |
| SQLite + sqlite-vec | Provisional |
| Drive・Policy既定値・SLO具体値・モデル選定 | Provisional |
| **記憶 DB の暗号化方式**（暗号化ビルドと sqlite-vec / FTS5 / PyInstaller の統合） | Provisional — **Phase 2 の最初に spike で確認する** → [contracts/privacy.md](contracts/privacy.md) 末尾 |
| **Activity の priority の値**（割り込み判定の**方式**は Confirmed → [ADR-024](decisions/ADR-024-activity-priority.md)） | Provisional |
| **推論スタックの取得方法**（Ollama は検出のみ / VAD は同梱 / STT モデルは実行時取得） | **Confirmed**〔rev.8〕→ [ADR-023](decisions/ADR-023-llm-runtime-and-model-acquisition.md) |
| WS プロトコルの具体スキーマ | Provisional |
| **公開配布を前提とすること・配布物の構成・クレジット義務** | **Confirmed**〔rev.6〕→ [licensing.md](licensing.md) |
| 個別コンポーネントのライセンスの理解（AivisSpeech の LICENSE 本文など） | Provisional → [licensing.md](licensing.md) §7 の未確認事項 |
| **プライバシー / データ保存の方針**（保存先・暗号化・保持期間・消去対象） | **Confirmed**〔rev.16〕→ [contracts/privacy.md](contracts/privacy.md) / [ADR-038](decisions/ADR-038-privacy-and-data-retention.md) |
| **Invariant 8 の実装方式**（全画面キャプチャ / 座標注入） | **未決 — Phase 4c 着手前に決める** |
| **DomainEvent の保持ポリシー** | **Confirmed**〔rev.16〕→ [contracts/privacy.md](contracts/privacy.md) §2（既定 30 日） |
| Audit log の hash chain | **Deferred (Phase 4a)** |
| Crash Recovery の実装 | **Deferred (Phase 4a)** — 語彙と型は確定済み |
| Model Resource Manager の実装 | **Deferred (Phase 5)** — 窓口は確定済み |
| Widget / Gamelet API の詳細 | **Deferred (Phase 7)** |
| Game Adapter / Skill / Observation スキーマ | **Deferred (Phase 8)** |
| Extension 署名・信頼レベルの実装 | **Deferred (Phase 9)** — manifest 欄は予約済み |

---

## 9. MVP と将来の拡張方針

### MVP = Phase 0 + Phase 1

**作るもの**: 透過ウィンドウに立つ VRM キャラクター。声で話しかけると理解し、ローカルLLM で考え、日本語で喋り、口が動き、**遮れば止まる**。同じセッション内の会話は覚えている。

**作らないもの**: 長期記憶 / 自律行動 / World Model / Internal State / ツール / Vision / PC操作 / ブラウザ / Widget / ゲーム / 第三者Extension / Live2D / クラウドLLM

**Kernel 基盤は Phase 1 に含める。** L0 ツールしか無くても、Attention Arbiter・Cancellation契約・Provenance・Event採番は本番と同じ型と経路を通す。**後から Kernel を入れるのが最も危険**であり、全コードのシグネチャ書き換えになるため。

**表情は Stretch。** リップシンクだけで会話は成立する。詰まったら最初にここを落とす。
**barge-in は絶対に落とさない。** 後付けが最も難しく Arbiter 設計に影響し、かつ中核的差別化点。

### Phase 一覧

| Phase | 内容 | 完了条件 |
|---|---|---|
| **0** | Walking Skeleton（AIなし） | 別マシンでインストーラから起動し、キャラが立って一言喋る |
| **1** | Talking Desktop Character（MVP） | p95 2.0秒以内に喋り始め、遮ると止まる |
| **2** | Memory | 記憶の形成・忘却・矛盾・検索が動き、ユーザーが記憶を見て直せる |
| **3** | World Model + Internal State + 自律（発話のみ） | **1日つけっぱなしにして不快でない** |
| **4a** | Kernel 本実装 + 権限 UI + Grant + 監査 + Crash Recovery + `fs` | **TOCTOU 攻撃テストが全部 deny される** |
| **4b** | `browser`（Class B） | scope 外へのリダイレクト結果が破棄され監査に残る |
| **4c** | `computer`（screenshot + input） | Invariant 8 の穴が塞がっている |
| **5** | Vision + Model Resource Manager | VRAM 競合下で Vision がオンデマンドに動く |
| **6** | Autonomous Life（3 × 4） | 自律 actor がツールを使い、何をしているかが常に見える |
| **7** | Widget / Gamelet / 生成ゲーム | AI がゲームを作り、Sandbox で動き、作り直せる |
| **8** | Game Agent | Strategy-Tactics-Reflex で実ゲームをプレイする |
| **9** | 第三者Extension / Live2D | Extension SDK 公開、Live2D Renderer 追加 |

詳細と各 Phase の作業内容 → [roadmap.md](roadmap.md)

### 将来の拡張ポイント

| 拡張したいもの | 用意してある仕組み |
|---|---|
| 別の LLM / STT / TTS / Embedding / Vision | `Provider` interface（`runtime: in-core`、[interfaces/provider.md](interfaces/provider.md)） |
| 別の能力（ブラウザ・ゲーム・センサ） | Capability Extension（`runtime: out-of-process`、**Class B の lane のみ**、[interfaces/extension.md](interfaces/extension.md)） |
| 新しい副作用ツール（fs / process / input 系） | in-core built-in Tool（Class A。[ADR-017](decisions/ADR-017-out-of-process-tool-contract.md)） |
| 別のキャラクター描画（Live2D / MMD） | `CharacterRenderer` interface（`runtime: stage`、[interfaces/renderer.md](interfaces/renderer.md)） |
| 別のベクトルDB（Qdrant等） | `VectorStore` interface（[interfaces/memory.md](interfaces/memory.md)） |
| 別のデスクトップシェル（Electron） | `PlatformShell` interface（[interfaces/shell.md](interfaces/shell.md)） |
| 新しいゲーム | `GameAdapter`（Phase 8 で設計） |
| 第三者による配布 | Extension manifest の `trust_level`（欄は予約済み、実装は Phase 9） |

---

## 10. AIRI との関係

### 位置づけ

**「クリーンルーム設計」とは呼ばない。** Clean-room implementation は「実装を見た人と書く人を分離する（Chinese wall）」という厳密な手続きを指す語であり、AIRI を読んだ上で設計している本プロジェクトには当てはまらない。正確には:

> **AIRI を参考実装・技術資料として調査し、その設計思想と運用知見を参照する。ただしコード・実装断片・固有プロトコル・データ形式・パッケージ構成は移植しない。**

AIRI は MIT であり移植しても法的問題はない。移植しないのは**技術的判断**（AIRI の構造が Lumi の要件に合わないため）である。

### 調査結果の要旨（2026-08、v0.11.3 / HEAD c71de3a）

AIRI は「マルチモーダル入出力パイプライン」としては完成度が高いが、**「記憶を持ち自律し安全に PC を操作するエージェント」としてはほぼ未着手**だった。

| AIRI で未実装 | 実態 |
|---|---|
| 長期記憶 | スキーマ定義のみ、**参照コードがゼロ**。`memory-pgvector` は27行のスタブ |
| 忘却・重要度 | `importance` / `last_accessed` 列は**一度も読み書きされない** |
| Agent Loop | plan→act→reflect は**存在しない**。1発話 = 1 LLM ストリーム |
| 自律行動 | 2秒 tick はあるが**ユーザー登録タスクのリマインダ配送のみ** |
| barge-in | **未実装**。逆に「発話中は音声入力を抑制」する保守的設計 |
| tool call 承認 | **完全に非存在**。IPC ハンドラが無ゲートで直呼び |
| Extension 隔離 | `await import()` で **Electron main プロセスに直接ロード**。Node フルアクセス |
| Extension 権限 | `permissionResolver` 未指定のため **manifest 宣言がそのまま自動 granted** |

### 借りるもの / 借りないもの

**借りる（設計思想・運用知見）**
1. ToolDescriptor レジストリ（lane/kind/readOnly/destructive/concurrencySafe/requiresApproval/deferred + fail-closed）
2. Extension 権限の交差モデル（宣言=天井 ∩ 実要求）
3. 透過ウィンドウ実運用ノウハウ（常時最前面、非フォーカス表示、backgroundThrottling無効化、コンテンツ保護、自分自身を deny リストに入れる）
4. TTS の文単位セグメント化 + 先読み並列生成 + 順序保証再生
5. LLM ストリーム内インラインマーカーによる表情/モーション制御
6. Minecraft 統合の perception / reflex / conscious 三層
7. Live2D SDK をリポジトリに入れずビルド時取得（Live2D 導入時）
8. ウィンドウ設定を純粋関数に切り出してユニットテストする作法

**借りない**
1. 約60種の WS イベントによるモジュールライフサイクル振り付け → 明示的 Command シーケンス
2. Extension を特権プロセスに `import()` → 第三者は常に別プロセス
3. manifest 権限の同意なし自動 grant → 初回同意 UI 必須
4. tool call 無ゲート実行 → Permission Kernel 必須（Invariant 2）
5. `allow-scripts allow-same-origin` の iframe sandbox → Broker を真の境界にする
6. UI ストアにビジネスロジック → ロジックは Core のみ
7. 48パッケージのモノレポ（空・スタブ多数） → 実体があるものだけ
8. 「発話中は音声入力を抑制」 → 真の barge-in を実装
9. AIRI のメモリ設計 → そもそも未実装。独自設計
10. クラウドサーバ/課金/マルチプラットフォーム → 非目標

### ライセンス方針

> **調査結果・配布物の構成・クレジット義務の唯一の定義場所は [licensing.md](licensing.md)。** ここには要約だけを置く。

原則:

> **Core のライセンス境界（MIT）を維持するため、LGPL 等のコンポーネントは原則として外部プロセス化する。ただし「別プロセス通信だからライセンス上の影響がない」と設計上断定はしない。各配布形態について個別にライセンス確認を行う。**

これに、rev.6 で1つ足す:

> **配布物には、再配布が明示的に許諾されているものだけを入れる。** 未確認のものは入れない（fail-closed）。**一度配布したものは取り消せない**ため → [ADR-019](decisions/ADR-019-tts-engine-distribution.md)

| 対象 | 現状の理解 | 配布物 |
|---|---|---|
| AIRI 本体 | MIT | — （参照のみ、移植しない） |
| `@pixiv/three-vrm` | MIT | ✓ |
| faster-whisper / Silero VAD | MIT系 | ✓ |
| sqlite-vec | Apache-2.0 / MIT | ✓ |
| Playwright | Apache-2.0 | ✓（Phase 4b） |
| Live2D Cubism Core | **非OSS** | **✗** Extension 境界に隔離。ビルド時取得（Phase 9） |
| **AivisSpeech / VOICEVOX Engine** | LGPL-3.0系（**AivisSpeech は LICENSE 本文が未確認**） | **✗ 実行時取得**〔2026-08-15 決定〕 |
| **音声ライブラリ（キャラクター音声）** | **調査済み**〔2026-08-15〕→ [licensing.md](licensing.md) | 条件付き（ACML 等は再配布可） |
| LLM / Embedding モデル | モデルごとに異なる | **✗**（選定時に確認） |

**設計上の担保**: `TTSProvider` / `LLMProvider` 抽象があるため、問題判明時は Provider を差し替えるだけで対応できる。差し替えたときにクレジット表示も追随するよう、`Provider.attribution()` を Phase 1 の interface に含める。

**保証しないこと**: [licensing.md](licensing.md) は法的助言ではなく、規約の原文を読んだ開発者の理解である。ACML 特例条項への対応が「十分な努力」と評価されることも保証しない（評価するのは権利者）。

---

## 11. ドキュメント索引

### ルート

| ファイル | 内容 |
|---|---|
| [roadmap.md](roadmap.md) | Phase 分割・完了条件・未確定事項・リスク一覧 |
| [licensing.md](licensing.md) | 外部コンポーネントのライセンス調査結果・**配布物の構成**・クレジット義務 |
| [measurements/](measurements/) | **実測値の記録**（設計ではなく観測）。R1 / R2 / R4 と Phase 5 の判定根拠 |

### contracts/ — 型と契約（すべて Confirmed。変更コストが最も高い）

| ファイル | 内容 |
|---|---|
| [invariants.md](contracts/invariants.md) | 8つの不変条件と、それぞれの根拠・検証方法 |
| [authority-matrix.md](contracts/authority-matrix.md) | 誰が何をできるか。実装レビューのチェックリスト |
| [security-boundaries.md](contracts/security-boundaries.md) | B1〜B7 の境界。攻撃者・認証・認可・検証 |
| [provenance.md](contracts/provenance.md) | ProvenanceClass / TrustLevel の束と伝播規則 |
| [state-machines.md](contracts/state-machines.md) | Activity と Tool の独立した状態機械 |
| [event-model.md](contracts/event-model.md) | Signal と DomainEvent の分離、採番責任、順序保証 |
| [tool-execution.md](contracts/tool-execution.md) | Kernel実行契約 canonicalize→decide→bind→verify→execute |
| [wire.md](contracts/wire.md) | 線上に出る名前と定数の規則。値は [wire.json](contracts/wire.json) が持つ |

### architecture/ — 各領域の設計

| ファイル | 内容 |
|---|---|
| [core.md](architecture/core.md) | Core の定義、プロセス構成、namespace、依存関係 |
| [agent.md](architecture/agent.md) | Attention Arbiter、Reactive Loop、Cancellation |
| [autonomy.md](architecture/autonomy.md) | Drive System、AutonomyGate、AutonomyBudget |
| [memory.md](architecture/memory.md) | 4層、assertion_mode、形成・忘却・矛盾・検索 |
| [world-state.md](architecture/world-state.md) | World State と Internal State の分離 |
| [permission.md](architecture/permission.md) | Risk階層、actor、Grant、Scope正規化、監査 |
| [extension.md](architecture/extension.md) | Provider / Capability の2機構、信頼レベル、Hook |
| [audio.md](architecture/audio.md) | barge-in critical path、EchoGuard、SLO |
| [ui.md](architecture/ui.md) | Shell、Stage、Widget Broker、Character |
| [setup.md](architecture/setup.md) | 初回セットアップ、外部エンジンの取得・検証・ロールバック・検出 |
| [recovery.md](architecture/recovery.md) | Crash Recovery、冪等性、イベント語彙 |

### interfaces/ — コンポーネント間の契約

| ファイル | 内容 |
|---|---|
| [tool.md](interfaces/tool.md) | `Tool` / `ToolContext` / `SecurityScope` / `Handle` |
| [extension.md](interfaces/extension.md) | manifest、ライフサイクル、protocol |
| [provider.md](interfaces/provider.md) | LLM / STT / TTS / Embedding / Vision Provider |
| [memory.md](interfaces/memory.md) | `MemoryStore` / `VectorStore` / `Retriever` |
| [renderer.md](interfaces/renderer.md) | `CharacterRenderer`、ExpressionIntent |
| [shell.md](interfaces/shell.md) | `PlatformShell`（Electron 退避路） |

### decisions/ — ADR

| ADR | タイトル |
|---|---|
| [001](decisions/ADR-001-desktop-shell-tauri.md) | Desktop Shell に Tauri 2 を採用し `PlatformShell` で抽象化する |
| [002](decisions/ADR-002-python-core-as-hub.md) | AI Core を Python 単一プロセスとし、Core をハブとする |
| [003](decisions/ADR-003-audio-in-core.md) | 音声 I/O を Core 側に置き、barge-in critical path を Core 内に閉じる |
| [004](decisions/ADR-004-sqlite-vec-memory.md) | Memory を SQLite + sqlite-vec とし `VectorStore` で抽象化する |
| [005](decisions/ADR-005-extension-two-mechanisms.md) | Extension を in-core Provider と out-of-process Capability に分ける |
| [006](decisions/ADR-006-kernel-execution-contract.md) | Kernel実行契約。Canonicalizer / decide / BindVerifier を Kernel 所有にする |
| [007](decisions/ADR-007-drive-system.md) | 自律行動を Drive System + AutonomyBudget で制御する |
| [008](decisions/ADR-008-provider-abstraction.md) | LLM/STT/TTS/Embedding/Vision を Provider interface で交換可能にする |
| [009](decisions/ADR-009-renderer-intent-based.md) | Character Renderer を「意図」ベースとし VRM 優先とする |
| [010](decisions/ADR-010-signal-vs-domain-event.md) | Signal と DomainEvent を分離し、DomainEvent 発行を Core に独占させる |
| [011](decisions/ADR-011-provenance-no-laundering.md) | Provenance を2層とし、No Laundering を保証する |
| [012](decisions/ADR-012-cancellation-contract.md) | Cancellation を3段階の契約とし、Activity と Tool の状態を独立させる |
| [013](decisions/ADR-013-memory-assertion-mode.md) | Memory に assertion_mode を導入し、LLM 抽出結果を無条件に信用しない |
| [014](decisions/ADR-014-world-vs-internal-state.md) | World State と Internal State を分離する |
| [015](decisions/ADR-015-core-shell-boundary.md) | Core→Shell をセキュリティ境界とし、Shell に拒否権のみを持たせる |
| [016](decisions/ADR-016-always-one-activity.md) | foreground Activity を常に1つとし、idle Activity を必置にする（一部を 018 が修正） |
| [017](decisions/ADR-017-out-of-process-tool-contract.md) | 副作用を持つ lane の Tool を in-core に置き、out-of-process には事後検証契約を適用する |
| [018](decisions/ADR-018-foreground-and-jobs.md) | foreground を単一の参照として定義し、Job を Activity と分離する |
| [019](decisions/ADR-019-tts-engine-distribution.md) | TTS エンジンを配布物に含めず、ユーザーの明示的な選択に基づく実行時取得とする |
| [020](decisions/ADR-020-split-audio-streams.md) | 入力と出力を別ストリームで開き、クロックドリフトを実測可能にする |
| [021](decisions/ADR-021-sidecar-packaging.md) | Python Core を PyInstaller の onedir で固め、resources として同梱する |
| [022](decisions/ADR-022-wire-contract.md) | プロセス境界を越える名前と定数を `wire.json` に一元化し、3言語のテストで突き合わせる |
| [023](decisions/ADR-023-llm-runtime-and-model-acquisition.md) | LLM ランタイムと推論モデルを配布物に含めず、種類ごとに取得方法を変える |
| [024](decisions/ADR-024-activity-priority.md) | Activity の priority を表から決め、割り込み可否を単一の閾値で判定する |
| [025](decisions/ADR-025-tts-on-gpu.md) | **TTS に GPU を使えるようにする**（ADR-008 の「VRAM を消費しない」を実測により撤回） |
| [026](decisions/ADR-026-polyphase-resampler.md) | サンプルレート変換を自前の polyphase FIR にし、VAD 経路では状態を持たせる（STT 精度の主因） |
| [027](decisions/ADR-027-stt-model-large-v3-turbo.md) | STT の既定モデルを `small` から `large-v3-turbo` にする（語彙混同。CER 半減） |
| [028](decisions/ADR-028-stage-initiated-request.md) | Stage → Core の要求方向（`request`）を作る。**`command` は片方向のまま**（Core が決める非対称を名前で保つ） |
| [029](decisions/ADR-029-content-pack-asset-delivery.md) | Content Pack のアセットは Shell が WebView に配信する（**Core をファイルサーバにしない**） |
| [030](decisions/ADR-030-per-stream-dispatch.md) | DomainEvent の配送を stream ごとに直列化し、**同一 stream への再入 publish を拒否する** |
| [031](decisions/ADR-031-request-side-effects.md) | `request` 経路が許す副作用を「**Core が所有する状態の変更**」に限定して定義する（ADR-028 条件4の修正） |
| [032](decisions/ADR-032-tts-speed.md) | 読み上げ速度を **Core 所有の設定**として TTS と Stage に反映する |
| [033](decisions/ADR-033-gate-voice-input-until-ready.md) | 起動フェーズが **`ready` になるまで音声入力を開始しない** |
| [034](decisions/ADR-034-gate-startup-on-complete-setup.md) | **STT / LLM / TTS がすべて揃うまでキャラクターを起動しない** |
| [035](decisions/ADR-035-separate-stt-installation-and-runtime-state.md) | **STT のモデル取得状態と Provider ロード状態を分離する** |
| [036](decisions/ADR-036-core-sends-reason-codes.md) | **Core は表示文字列を送らない。**理由はコードで送り、翻訳は Stage が持つ |

---

## 12. 単一定義（SSoT）の規則

> **同じ内容を2箇所に書かない。** ドキュメントが9,000行を超えた時点で、人間も AI も全部を同期させ続けることはできない。

### 規則

1. 各項目について**唯一の定義場所**を決め、他のファイルは**リンクする**
2. リンク先の要点を1〜2行で再掲するのは可。**表や式やコードを再掲しない**
3. **ADR は例外。** ADR は決定時点の記録なので、内容が重複していても更新しない（修正が必要なら新しい ADR を書き、古い ADR に「修正された」旨を追記する）
4. 新しい表・式・型定義を書くときは、**まずこの表に行が要るかを確認する**

### 定義場所の一覧

| 項目 | 唯一の定義場所 |
|---|---|
| 8つの Invariant | [contracts/invariants.md](contracts/invariants.md) |
| Policy（`decide()` / Risk 階層 / actor） | [architecture/permission.md](architecture/permission.md) |
| Provenance の型・伝播・trust の3スコープ・隔離ブロック書式 | [contracts/provenance.md](contracts/provenance.md) |
| Activity / Tool の状態機械・foreground の定義・Cancellation 3契約・barge-in 手順 | [contracts/state-machines.md](contracts/state-machines.md) |
| **Activity の `priority` / `interruptible_at` の値と割り込み判定** | [architecture/agent.md](architecture/agent.md) §1 |
| **PromptAssembly の構成・トークン予算・切り落とし順序** | [architecture/agent.md](architecture/agent.md) §3 |
| Kernel 実行契約・Class A / Class B | [contracts/tool-execution.md](contracts/tool-execution.md) |
| Signal / DomainEvent / Command・**Hook 一覧** | [contracts/event-model.md](contracts/event-model.md) |
| 境界 B1〜B7・Widget Broker と iframe sandbox・監査ログの append-only の意味 | [contracts/security-boundaries.md](contracts/security-boundaries.md) |
| 権限マトリクス・オブジェクト責務行列 | [contracts/authority-matrix.md](contracts/authority-matrix.md) |
| **線上に出る名前と定数**（`PROTOCOL_VERSION` / method 名 / Tauri のイベント・コマンド名 / 線に乗る enum の値） | [contracts/wire.json](contracts/wire.json)（値）+ [contracts/wire.md](contracts/wire.md)（規則） |
| Shell / Stage の責務・ウィンドウ一覧・**トレイメニュー**・**起動フェーズ**・**ウィンドウ操作**・Tauri 2 の課題・AIRI 運用知見・表情の合成 | [architecture/ui.md](architecture/ui.md) |
| 音声の3層構造・EchoGuard・VAD パラメータ・**レイテンシ SLO**・**デバイス選択とストリームの開き方** | [architecture/audio.md](architecture/audio.md) |
| 記憶の形成・忘却・矛盾・**検索スコアリング式**・salience 補正 | [architecture/memory.md](architecture/memory.md) |
| **永続化されるものの一覧・保存先・暗号化・保持期間・消去対象** | [contracts/privacy.md](contracts/privacy.md) |
| Extension の2機構・信頼レベル・ライフサイクル・Content Pack | [architecture/extension.md](architecture/extension.md) |
| Drive / AutonomyGate / AutonomyBudget | [architecture/autonomy.md](architecture/autonomy.md) |
| World / Internal State の分離と facet 定義 | [architecture/world-state.md](architecture/world-state.md) |
| `Tool` / `SecurityScope` / `Handle` / 検証器の**型定義** | [interfaces/tool.md](interfaces/tool.md) |
| `MemoryRecord` / `AssertionMode` の**型定義** | [interfaces/memory.md](interfaces/memory.md) |
| Renderer に渡す意図の型・**リップシンクの生成方式**・`stage.speech.*` の契約 | [interfaces/renderer.md](interfaces/renderer.md) |
| GPU / VRAM 戦略とモデル配置 | **DESIGN.md** §7 |
| Phase 分割と完了条件・未確定事項・リスク一覧 | [roadmap.md](roadmap.md) |
| **外部コンポーネントのライセンス・配布物の構成・クレジット義務・ACML 特例への対応** | [licensing.md](licensing.md) |
| **実測値**（インストーラサイズ / RAM / VRAM / レイテンシ / CPU） | `measurements/phase<N>.md` |
| **外部エンジンの取得元のピン留め・検証方法・インストール先・TTS セットアップ状態** | [architecture/setup.md](architecture/setup.md) |
