# Lumi — 開発ロードマップ

> 各 Phase は**単体で使える製品**であること。Phase 1 で止めても「喋るデスクトップキャラ」として成立する。
> このプロジェクトは規模が大きい。完成を待たずに価値が出る分割になっていることが、完成させるための条件である。

関連: [DESIGN.md](DESIGN.md)

---

## Phase 分割の方針と、当初案からの変更

ユーザー当初案（Talking → Memory → Vision → Computer Use → Autonomous → Game → Generated Games → 3rd-party）から **3点変更**した。

### 変更1: Phase 0（Walking Skeleton）を独立させた
Tauri の透過+クリックスルー、Python サイドカーのパッケージングは、**AI 要素と無関係に失敗しうる**。MVP に混ぜると原因の切り分けができなくなる。

### 変更2: 自律行動を「発話のみ」(Phase 3) と「行動」(Phase 6) に分割した
自律の難しさは **「鬱陶しさの調整」と「安全性」の2つ**で、独立した問題。前者は権限システム無しで解ける。同時にやるとどちらの失敗か切り分けられない。

### 変更3: Widget/生成ゲーム (Phase 7) を Game Agent (Phase 8) の前に置いた
生成ゲームはサンドボックス基盤があれば実現でき、Game Agent（CV・プランナ・アダプタ）より遥かに小さい。Widget インフラは権限 UI や GameAgent の可視化にも再利用される。

### 変更4: Phase 4 を 4a / 4b / 4c に分割した〔2026-08-15〕
Phase 4 は Kernel 本実装・権限 UI・Grant・監査・hash chain・Crash Recovery・fs・browser・computer を全部含んでおり、**最も危険な Phase に最も多くを詰め込んでいた**。

分割の軸は「**契約の実効性を最小コストで検証できる単位**」。

- **4a**: Kernel 本実装 + 権限 UI + Grant + 監査 + Crash Recovery + **`fs` のみ**
- **4b**: `browser`（Class B。事後検証契約の初適用）
- **4c**: `computer`（screenshot + input injection）

**4c を最後に置くのは、Invariant 8 の穴（全画面キャプチャ / 座標指定の入力注入）の決着が前提だから。**

---

## Phase 0 — Walking Skeleton

**目的: 賢さゼロで、危険な統合点を先に全部貫通させる。**

### 最初にやること（他がこれに依存する）
- [x] **AivisSpeech / VOICEVOX の音声ライブラリ利用規約を確認して記録** 〔2026-08-15 完了 → [licensing.md](licensing.md)〕
- [x] **確認結果を受けて「同梱するか / ユーザーに別途インストールさせるか」を決める** 〔2026-08-15 決定 → [ADR-019](decisions/ADR-019-tts-engine-distribution.md)〕

**決定: 配布物に含めない。** ユーザーの明示的な選択に基づき、初回セットアップで公式配布元から取得する。
Lumi は**公開配布される前提**で設計する。これによりクレジット表記が Phase 0 の必須項目になった（下記）。

### やること
- [x] Tauri 2 で透過ウィンドウ・常時最前面・クリックスルー 〔2026-08-15。透過の目視確認は残〕
- [x] **ホバー検知**（Rust 側で Win32 カーソル監視の自前実装。Tauri には Electron の `forward:true` 相当が無い）〔判定は Shell 側の純粋関数。[measurements/phase0.md](measurements/phase0.md)〕
- [ ] VRM を Stage に表示し、アイドルモーションでループ 〔**プレースホルダで貫通済み**。VRM ローダーの統合点は実装済みで、`.vrm` を置けば読む。既定同梱モデルは未定（[licensing.md](licensing.md) §7 未確認 #5）〕
- [x] Python Core を **Tauri サイドカーとして起動・監視・終了** 〔Job Object でゾンビを残さない〕
- [x] WS 接続（token 認証）、ハートビート、片方が落ちた時の復帰
- [x] **`os.*` の schema 検証 + allowlist を Shell 側に最小実装**（[B3](contracts/security-boundaries.md) の骨格）
- [ ] **初回セットアップ**〔ADR-019〕
  - [ ] TTS エンジンを取得する / しない の選択を**同等に**提示する（既定で取得しない）
  - [ ] 公式配布元からの取得 → **検証**（完全性・配布元）→ セットアップ
  - [ ] 失敗時のロールバック（**部分的にインストールされた状態を残さない**）
  - [ ] 取得しない場合も Lumi が起動し、**「TTS 未セットアップ」が明示される**
  - [ ] ユーザーが別途インストール済みの AivisSpeech / VOICEVOX を検出する
- [ ] **クレジット表示画面**（トレイ → クレジット）〔ADR-019〕
  - [ ] エンジン名・音源名・ライセンス全文・禁止事項をユーザーが読める
  - [ ] Phase 0 では Stage 側に静的に作る。`Provider.attribution()` との接続は Phase 1
- [ ] ハードコードされた「こんにちは」を AivisSpeech で発話 → リップシンク
- [ ] duplex stream の骨格（capture + playback + reference channel）
- [ ] **入出力が別デバイスのときの duplex 動作を実測**（別デバイスだと失敗 / クロックドリフトする。Phase 2 の AEC の前提が崩れないか）
- [x] `PlatformShell` インターフェースを定義（Electron 退避路の確保）〔Stage に露出するのは OS 特権を含まない部分集合。[interfaces/shell.md](interfaces/shell.md)〕
- [ ] **VRAM / RAM / インストーラサイズを実測して記録**（Phase 5 の設計根拠になる）→ [measurements/phase0.md](measurements/phase0.md)
- [ ] **サイドカー同梱状態で sqlite-vec（SQLite ローダブル拡張）がロードできることを確認**（Phase 2 で気づくと記憶機能ごと止まる）〔**uv 環境では確認済み**。同梱サイドカーでの確認が残り〕

### 完了条件
インストーラを作って**別マシン**で起動し、**初回セットアップを通した上で**、キャラクターが立って一言喋る。
**TTS の取得を拒否しても Lumi は起動する**（喋らないが、壊れていない状態として明示される）。

> 初回セットアップの実装が Phase 0 を圧迫する場合は、**完了条件を「TTS は完全手動インストール前提」に緩め、取得フローを Phase 1 に送る**（[ADR-019](decisions/ADR-019-tts-engine-distribution.md) Alternative B への退避）。
> **ただしクレジット表示は落とさない。** 配布物ができた時点で義務が発生し、後から遡って直せないため。

### 検証手順
1. `pnpm tauri build` で Windows インストーラが生成され、**サイズを記録**（R1判定）
2. クリーンな別マシン（または VM）にインストールして起動
3. キャラクターが透過背景で最前面に立ち、**背後のウィンドウが操作できる**
4. キャラクターの上にカーソルを乗せると**ホバーが検知される**（R2判定）
5. タスクマネージャで Shell と Python Core の**両プロセスの RAM 使用量を記録**
6. 「こんにちは」を発話し、**口が動く**
7. Core プロセスを強制終了 → Shell が検知して再起動する
8. Shell を終了 → Core も確実に終了する（ゾンビプロセスが残らない）
9. **未知の `os.*` コマンドを Shell に送ると拒否され、ログに残る**（B3 骨格の確認）
10. **アイドル時の VRAM / RAM 実測値を記録**
11. ~~音声ライブラリ利用規約を確認し、記録~~ 〔2026-08-15 完了 → [licensing.md](licensing.md)〕
12. **マイクとスピーカーが別デバイスの構成で duplex stream が開けるか確認**（開けないならフォールバック方針を決めて記録）
13. **サイドカーから sqlite-vec がロードできるか確認**
14. **インストーラに AivisSpeech / VOICEVOX のバイナリが含まれていないことを確認**（[licensing.md](licensing.md) テスト1）
15. **初回セットアップで「取得しない」を選び、Lumi が起動して「TTS 未セットアップ」が表示されることを確認**
16. **ネットワークを遮断した状態で取得を試み、明示的に失敗し、部分的な残骸が残らないことを確認**
17. **ユーザーが選択するまで外部へのネットワークアクセスが発生しないことを確認**（パケットキャプチャ / Network-optional 原則）
18. **クレジット画面にエンジン名・音源名・ライセンス全文が表示されることを確認**

### ここで失敗したら
Shell 選定（Tauri → Electron）とパッケージング方針を見直す。`PlatformShell` 抽象があるので Stage 側の実装は流用できる。

---

## Phase 1 — MVP: Talking Desktop Character

**目的: 「話しかけると答え、遮れば止まる」を成立させる。**

### 必須
- [ ] Mic → Silero VAD → faster-whisper → Ollama(Qwen3系) → AivisSpeech → 再生 + **リップシンク**
- [ ] **音声の3層分離**（audio callback / VAD 専用スレッド / asyncio）。**コールバック内で推論しない**
- [ ] **barge-in**（ミュート判定と発話区間確定を別閾値で。誤爆から復帰できること）
- [ ] EchoGuard **L1**（適応的閾値。ヘッドホン前提を明示）
- [ ] Working Memory のみ（セッション内会話履歴。ベクトルなし）
- [ ] **Kernel 基盤**
  - [ ] Attention Arbiter（`_foreground` 単一参照 / idle 必置 / `suspended`）
  - [ ] `DeferredQueue`（TTL 付き）
  - [ ] **`Job` と `inference_lease`**（Phase 2 の Reflection で必要になるが、後から入れると経路が変わる）
  - [ ] Activity / Tool の独立した状態機械
  - [ ] Cancellation 契約（cooperative / hard / non_cancellable）
  - [ ] Command / EventBus（Signal ↔ DomainEvent 分離、stream_key 採番）
  - [ ] Hook（固定セット）
  - [ ] Crash Recovery の**イベント語彙**と `idempotency_key` の型（実装は Phase 4a）
- [ ] **Permission Kernel の骨格**（L0 ツールのみ登録。`decide()` は本番と同じ関数。Kernel実行契約と Scope 正規化も本番と同じ経路）
- [ ] **Provenance の型と伝播**（L0 しか無くても型を後入れしない）
  - [ ] `block_trust` / `history_trust` / `session_trust`（sticky）の3スコープ
- [ ] Provider interface（`load` / `unload` / `resource_hint` を含む）
- [ ] 構造化ログ（structlog）+ SLO 計測（p50/p95/p99、**`unaccounted_ms` を含む**）
- [ ] Inspector 最小版（Activity ツリー / レイテンシ内訳）

### Stretch（詰まったら落とす）
- [ ] 表情変化（`<|ACT|>` マーカー → ExpressionIntent → VRM ブレンドシェイプ）

### 完了条件
話しかけると **p95 2.0 秒以内**に喋り始め、**途中で遮ると止まる**。

### レイテンシ SLO

**表と区間別予算は [architecture/audio.md](architecture/audio.md) §7 が唯一の定義場所。**

要点: p50 < 1.2s / p95 < 2.0s / p99 < 3.0s。**区間合計は p50 目標の 85% 以下**に収め、残りを計測外処理の予備枠として空けておく（Phase 1 は区間合計 0.95s）。

### なぜ Kernel 基盤を MVP に入れるのか
**あとから Kernel を入れるのが一番危ない。** Attention Arbiter・Cancellation 契約・Provenance・Event 採番は、後から挿入すると全コードのシグネチャを書き換えることになる。L0 ツールしか無くても、**型と経路は本番と同じもの**を通す。

`Job` / `inference_lease` も同じ理由で Phase 1 に入れる。実際に使うのは Phase 2（Reflection）だが、後から入れると LLM 呼び出し経路が全部変わる。

### 落とすなら
表情から。**barge-in は絶対に落とさない**（後付けが最も難しく Arbiter 設計に影響し、かつ中核的差別化点）。

---

## Phase 2 — Memory

**目的: 「覚えていて、忘れて、思い出す」を成立させる。**

### 🔴 着手前に決めること — プライバシーとデータ保存

**Phase 2 に着手する前に `contracts/privacy.md`（仮）を書く。** Phase 2 は「ユーザーの発話を永続化する」Phase であり、**書き始めてから方針を決めると、スキーマもマイグレーションも作り直しになる。**

Lumi は常時マイクを開き、会話の生ログを永続化し、前面アプリを継続観測し、全操作を監査ログに残す。それにもかかわらず、現在の docs には次の方針が**どこにも無い**。

| # | 決めること |
|---|---|
| 1 | データの保存場所と、**暗号化するか**（記憶 DB は平文 SQLite でよいか） |
| 2 | **Episode（会話の生ログ）の保持期間**。記憶は archive / decay するが、元ログは永久保存なのか |
| 3 | アンインストール時の挙動（データを残すか消すか、ユーザーに聞くか） |
| 4 | **第三者の音声**。部屋の中の他人の発話も STT され、永続化されうる（[contracts/provenance.md](contracts/provenance.md) が限界として記録済み）。録音・保存の是非は別問題として決める |
| 5 | ユーザーが「全部消したい」と言ったときに応えられる構造か |
| 6 | 監査ログの保持期間（append-only だが無限に貯まる） |

**方針として既に決まっているもの**（新文書に集約する）:
- `user.focus_app` は取るが**ウィンドウタイトルは取らない**（[architecture/world-state.md](architecture/world-state.md)）
- 監査ログには `raw_input_digest` を入れ、生の値は残さない（[architecture/permission.md](architecture/permission.md)）
- 記憶の物理削除はユーザーの明示操作でのみ（[architecture/memory.md](architecture/memory.md)）

### やること

- [ ] SQLite + sqlite-vec + FTS5 + マイグレーション基盤（`_schema_version`）
- [ ] Embedding Provider（ONNX / CPU）
- [ ] Episode 記録（会話の生ログ）
- [ ] **Reflection Job**（セッション終了時 / 長いアイドル時。**`Job` として実行し、`inference_lease` を取る**）
- [ ] **assertion_mode / evidence / provenance の実装**
- [ ] salience の決定論的補正（感情強度・新規性・明示・反復・参照回数）
- [ ] 減衰とアーカイブ（物理削除しない）
- [ ] 矛盾の supersede（`valid_from` / `superseded_by`）
- [ ] ハイブリッド検索（vector + FTS5 + recency + salience + assertion_mode）+ トークン予算
- [ ] **記憶の閲覧・編集・削除 UI**

### 完了条件
数日使って、Lumi が過去の会話を正しく思い出し、古い話題は自然に薄れ、矛盾したときに「前はこう言ってたよね」と言える。

### なぜ記憶 UI が必須なのか
- ユーザーが誤った記憶を直せないと、間違いが永久に残る
- **`user_confirmed` への昇格経路**であり、`tainted → trusted` の唯一の昇格経路でもある（[Invariant 7](contracts/invariants.md)）

---

## Phase 3 — World Model + Internal State + 自律（発話のみ）

**目的: 「自分から話しかけてくるが、鬱陶しくない」を成立させる。**

- [ ] Sensor Extension（foreground app / idle / presence / time）— out-of-process
- [ ] WorldState facet（TTL / confidence）+ プロンプトへの投影
- [ ] **Internal State**（mood / fatigue / arousal / attention_focus / drives）
- [ ] Drive System（social / curiosity / duty / play）
- [ ] AutonomyGate（在席 / DND / cooldown / quiet hours / budget / permission）
- [ ] AutonomyBudget（時間あたり割り込み回数 / トークン / wall-clock）
- [ ] **自律的な発話のみ。OS 操作はまだしない**
- [ ] 「うるさい」フィードバックループ（予算即時消費 + Drive 強制減衰 + Memory 書き込み）
- [ ] Inspector に Drive 内訳と「なぜ発火した/しなかったか」を表示

### 完了条件
**1日つけっぱなしにして不快でない。**

これはベンチマークではなく**実際の同居体験**を品質基準にしている。このプロジェクトは「何ができるか」より「一緒にいて嫌じゃないか」の方が重要であるため。

**これが通らない限り Phase 6 に進まない。**

---

## Phase 4 — Tools + Permission（本番）

**目的: 「PC を操作できるが、危険なことは必ず聞く」を成立させる。**

**最も危険な Phase なので 3 つに割る。** 各段階が単独で完了条件を持つ。

### Phase 4a — Kernel 本実装 + `fs`

- [ ] Tool Registry（ToolDescriptor メタデータ必須、fail-closed）
- [ ] `deferred` + `tool_search` メタツール（コンテキスト肥大対策）
- [ ] **Canonicalizer の本実装**（realpath / traversal / UNC / URL / IDN / リダイレクト事前解決）
- [ ] **BindVerifier の本実装**（`fs` lane）
- [ ] 権限プロンプト UI
- [ ] Grant（capability + security_scope + TTL + remaining_uses）
- [ ] 監査ログ閲覧 UI（`policy_version` / `policy_rule_id` 付き）
- [ ] **Audit log の hash chain**（`prev_hash` / `record_hash`）
- [ ] **Crash Recovery / idempotency の実装**
- [ ] **`fs.*` を in-core built-in Tool として実装**（Class A。Extension にしない → [ADR-017](decisions/ADR-017-out-of-process-tool-contract.md)）

**完了条件: TOCTOU 攻撃テストが全部 deny される。**
symlink 張り替え / traversal / UNC / 正規化失敗 のいずれでも `execute` に到達しないこと。これが**契約が実効的であることの最小の証明**であり、4b / 4c の前提になる。

### Phase 4b — `browser`（Class B の初適用）

- [ ] **`ResultVerifier` の実装**（`browser` lane。最終 URL が scope 内か）
- [ ] Browser Extension（Playwright, out-of-process）
- [ ] Class B の risk 下限検証（副作用ありは `risk >= L3` 固定）

**完了条件: リダイレクトで scope 外に出た結果が破棄され、監査に残る。**

### Phase 4c — `computer`（最も危険）

- [ ] **Invariant 8 の穴の決着**（下記。**これが先**）
- [ ] `computer.*` を in-core built-in Tool として実装（Class A）
- [ ] `input` lane の BindVerifier + Shell 側の二重拒否

#### 🔴 着手前に決めること — Invariant 8 の実装方式

**「対象ウィンドウが保護対象なら拒否」だけでは守れない。** → [contracts/invariants.md](contracts/invariants.md) の Invariant 8

| # | 穴 | 検討中の対処 |
|---|---|---|
| 1 | 全画面キャプチャに権限プロンプトが写る | 保護対象に `WDA_EXCLUDEFROMCAPTURE` を**常時**適用する |
| 2 | `SendInput` は HWND ではなく**座標**に届く | 注入直前に Shell 側で `WindowFromPoint` を評価する |
| 3 | 上記の判定漏れ | **権限プロンプト表示中は `os.input.*` を一律凍結する** |

**この決着まで `computer.*` を実装しない。**

### 最大のリスク
**`computer.*` は本質的にキーロガーと画面録画の能力そのもの。** L3固定 + scope 付き Grant + 全操作監査 + 自律 actor は deny + Invariant 8 で守るが、ここが最も慎重に実装すべき箇所。

---

## Phase 5 — Vision + Model Resource Manager

- [ ] Phase 0-1 の VRAM 実測値に基づく `ModelResourceManager` 本実装
- [ ] admission 制御 / LRU 退避 / pinning / 予約
- [ ] VLM のオンデマンドロードとアンロード
- [ ] `vision.observe(region?, question)` ツール
- [ ] screenshot hash によるキャッシュ（同じ画面なら再推論しない）
- [ ] 呼び出し予算（時間あたり回数）
- [ ] Vision 結果は必ず `ProvenanceClass = untrusted`

### 完了条件
LLM が GPU に載ったまま Vision がオンデマンドで動き、載らない時は**黙って遅くならず明示的に失敗する**。

---

## Phase 6 — Autonomous Life（Phase 3 × Phase 4）

**目的: 自律行動がツールを使えるようにする。ここで初めて actor による権限昇格が効く。**

- [ ] 自律 actor によるツール使用
- [ ] Goal / Plan / Execute / Observe / Reflect の完全ループ
- [ ] 自律活動の可視化（**何をしているかが常に見える**）
- [ ] 自律活動の中断・巻き戻し

### 完了条件
Lumi が自分で調べ物をして、その結果を後で話題にでき、かつ**ユーザーが「今何してるの」と聞けば即座に答えられる**。

### 前提
Phase 3 の完了条件（1日つけっぱなしで不快でない）を満たしていること。

---

## Phase 7 — Widget / Gamelet / 生成ゲーム

- [ ] Widget Broker（**真のセキュリティ境界**。iframe sandbox は多層防御の一枚に過ぎない）
- [ ] sandboxed iframe（`allow-scripts` のみ、opaque origin）
- [ ] Widget manifest と capability 宣言
- [ ] Widget API（`open` / `configure` / `request` / `close`）
- [ ] AI 生成ゲーム（HTML + TS）→ Sandbox 実行
- [ ] 生成ゲームの追加制約（ネットワーク完全遮断 / filesystem なし / shell なし / 実行時間・メモリ上限）
- [ ] 「もっと難しくして」→ コード変更 → 再ビルド → 再プレイ

---

## Phase 8 — Game Agent

- [ ] `GameAdapter` / `GameObservation` / `SkillDescriptor` の設計（**ここで初めて行う**）
- [ ] Strategy (LLM, 秒〜分) — Goal 生成・方針決定・再計画
- [ ] Tactics (Planner, 〜1秒) — Goal → Skill 列。決定論的
- [ ] Reflex (Controller, フレーム) — 危険回避・プリミティブ実行。LLM 非依存
- [ ] `ComputerAdapter`（汎用。screenshot + CV/OCR + input）
- [ ] `NativeAdapter`（Minecraft → Factorio）

### なぜ設計を Phase 8 まで遅らせるのか
実際に Minecraft / Factorio に触れば**必ず設計変更が入る**。今決めても捨てることになる。
今確定させているのは3点のみ:
1. 三層制御（LLM を毎フレーム呼ばない）
2. GameAgent は常に out-of-process Capability Extension
3. Core は `GameAdapter` インターフェースしか知らない

---

## Phase 9 — 第三者Extension / Live2D

- [ ] Extension SDK の公開
- [ ] manifest 署名
- [ ] 信頼レベル（`official` / `verified` / `untrusted`）の実装
- [ ] Extension インストール UI と権限同意フロー
- [ ] **第三者製 Provider を許すかの判断**（許す場合は out-of-process Provider という第3の機構が必要）
- [ ] Live2D RendererExtension（Cubism Core はビルド時取得、リポジトリに入れない）
- [ ] Live2D のライセンス区分確認

---

## 未確定事項（Phase ごとに解消する）

**🔴 = その Phase に着手する前に決着させる（着手してから決めると作り直しになる）**

| # | 事項 | 解消 |
|---|---|---|
| 1 | ~~🔴 AivisSpeech / VOICEVOX の音声ライブラリ利用規約~~ | **✓ 解消**〔2026-08-15〕→ [licensing.md](licensing.md), [ADR-019](decisions/ADR-019-tts-engine-distribution.md) |
| 1b | **AivisSpeech の LICENSE 本文・依存ライブラリ・同梱モデルのライセンス**（同梱を再検討する場合に必要） | Phase 0（取得実装時）→ [licensing.md](licensing.md) §7 |
| 2 | Python サイドカーのパッケージング（PyInstaller vs uv 同梱） | Phase 0（実測） |
| 3 | Ollama を同梱するかユーザーに別途インストールさせるか（**[ADR-019](decisions/ADR-019-tts-engine-distribution.md) と同じ論法を適用予定**） | Phase 0-1 |
| 4 | 入出力が別デバイスのときの duplex stream の扱い | Phase 0（実測。Phase 2 の AEC の前提） |
| 5 | LLM モデル選定（Qwen3系 / Gemma3系）— 日本語会話品質と Tool Calling 品質 | Phase 1（実測） |
| 6 | 🔴 **プライバシーとデータ保存の方針**（`contracts/privacy.md` を書く） | **Phase 2 着手前** |
| 7 | Embedding モデル（Ruri v3系 vs bge-m3）— 日本語検索品質 | Phase 2（実測） |
| 8 | **DomainEvent の保持ポリシー**（`world:*` の高頻度ストリームが無限に貯まる） | Phase 3 着手前 |
| 9 | 設定の保存形式とスキーマ | Phase 1（必要になった時点） |
| 10 | **`Activity.priority` の数値体系と `interruptible_by` を集合にする必要性** | Phase 1（Arbiter 実装時） |
| 11 | キャラクター人格の記述形式（独自 vs 既存カード互換） | Phase 1 後半 |
| 12 | Canonicalizer / BindVerifier の具体的アルゴリズム | Phase 4a |
| 13 | 🔴 **Invariant 8 の実装方式**（全画面キャプチャ / 座標指定の入力注入） | **Phase 4c 着手前** |
| 14 | **`sensor-desktop` が out-of-process のまま OS を直接読むこと**の是非（[contracts/authority-matrix.md](contracts/authority-matrix.md) は「OS 特権は Shell のみ」と書いている）。Shell に取り込む / `os.*` 経由にする / 表を直す のいずれか | Phase 3 着手前 |
| 15 | 多モニタ・混在 DPI での座標系（ヒットテストと入力注入） | Phase 4c |
| 16 | 第三者製 Provider を許すか | Phase 9 |
| 17 | Live2D 導入時のライセンス区分 | Phase 9 |

---

## 重大リスク一覧

| # | リスク | 影響 | 対策 | 判定 Phase |
|---|---|---|---|---|
| R1 | **Python Core の同梱**。torch 依存だとインストーラ 1-2GB | 配布不能 | torch を避ける（CTranslate2 / ONNX） | Phase 0 |
| R2 | **Tauri 2 の透過+クリックスルー+ホバー検知** | Shell 選定の破綻 | Phase 0 スパイク。`PlatformShell` で退避 | Phase 0 |
| R3 | **AEC 不在による barge-in の自己ループ** | 中核機能の破綻 | 3段階 EchoGuard | Phase 1-2 |
| R4 | **VRAM 競合** | 機能の相互排他 | TTS を CPU 別プロセスに。Phase 5 で Manager | Phase 0-1 実測 |
| R5 | **自律行動の鬱陶しさ** | 使われなくなる | Drive+Gate+Budget を決定論的に。完了条件を体験ベースに | Phase 3 |
| R6 | **ローカル 8B の Tool Calling 品質**（特に日本語） | Phase 4 以降の実用性 | 制約デコーディング。難所はクラウドに逃がす。タスク別モデル | Phase 4 |
| R7 | **プロンプトインジェクション** | セキュリティ | Provenance + No Laundering + Scope正規化 + L3+強制昇格 + `session_trust` の sticky 化 | 全 Phase |
| R8 | **Scope 正規化の漏れ**（symlink / redirect / DNS rebinding / homograph） | 権限バイパス | Kernel実行契約。fail-closed。多層防御 | Phase 4a |
| R9 | **Core 侵害時の Shell 経由の被害** | 最悪ケース | B3 の検証層 + Invariant 8 | Phase 0（骨格）/ Phase 4c |
| R15 | **オーディオコールバックのリアルタイム制約違反**（推論・GIL 競合によるアンダーラン） | barge-in と再生品質の破綻 | 3層分離（callback / VAD スレッド / asyncio）。負荷時テスト | Phase 1 |
| R16 | **Job（Reflection）が推論資源を占有し barge-in を裏切る** | SLO 未達 | `inference_lease` による調停 | Phase 2 |
| R17 | **out-of-process では bind/verify が成立しない** | 契約が文書上のものになる | Class A / B の分離。副作用 lane は in-core | Phase 4a-4c |
| R18 | **常時録音・全ログ保存のプライバシー方針が無い** | 設計のやり直し / 信頼の喪失 | Phase 2 着手前に `contracts/privacy.md` | Phase 2 |
| R10 | **プロジェクト規模の過大さ** | 完成しない | 各 Phase が単体で使える製品であること | 全 Phase |
| R11 | **レイテンシ SLO 未達** | 会話が不自然 | 区間別 p50/p95/p99 計測を最初から | Phase 1 |
| R12 | **`non_cancellable` が barge-in を裏切る** | UX 破綻 | Cancellation 契約。副作用ありは L3以上固定。`abandoned` 状態を明示 | Phase 1 |
| R13 | **Crash 中の副作用の宙ぶらりん** | 状態不整合 | イベント語彙を Phase 1 で固定。非 idempotent は再実行せず報告 | Phase 4a |
| R14 | **音声ライブラリ・モデルのライセンス** | 配布時の法務 | ~~Phase 0 で確認~~ → **確認済み**。配布物に非再配布可のものを入れない（[ADR-019](decisions/ADR-019-tts-engine-distribution.md)）。Provider 抽象で差し替え可能 | **Phase 0（対応済み。残: [licensing.md](licensing.md) §7 の未確認事項）** |
| R19 | **ACML 特例条項の「開発元の努力」義務**。LLM が任意テキストを生成して TTS に流す構成そのものが対象。努力が不十分と評価されるリスク | 音声モデルが使えなくなる | 人格プロンプト・なりすまし機能の不在・[Invariant 3](contracts/invariants.md) による外部テキスト隔離・クレジット/禁止事項の提示（[licensing.md](licensing.md) §5）。**内容分類器は作らない**（レイテンシと誤検知のコストが上回る） | Phase 1 |
