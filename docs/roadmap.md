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
- [x] VRM を Stage に表示し、アイドルモーションでループ 〔ローダーの統合点は実装済みで、`.vrm` を置けば読む。
  **既定同梱モデルを決定**〔2026-08-16。再配布 OK / 改変 OK / クレジット不要 / VRM 0.0 → [licensing.md](licensing.md) §4.5〕。
  **モデルファイルを `content/` に置く作業は残る**（リポジトリにはコミットしない）〕
- [x] Python Core を **Tauri サイドカーとして起動・監視・終了** 〔Job Object でゾンビを残さない〕
- [x] WS 接続（token 認証）、ハートビート、片方が落ちた時の復帰
- [x] **`os.*` の schema 検証 + allowlist を Shell 側に最小実装**（[B3](contracts/security-boundaries.md) の骨格）
- [x] **初回セットアップ**〔ADR-019 / 設計は [architecture/setup.md](architecture/setup.md)〕
  - [x] TTS エンジンを取得する / しない の選択を**同等に**提示する（既定で取得しない）
  - [x] 公式配布元からの取得 → **検証**（URL・サイズ・SHA-256）→ セットアップ
    〔2026-08-15 実ネットワークで取得を確認。216.5 MB / 8.4 秒、SHA-256 一致 → [measurements/phase0.md](measurements/phase0.md)〕
  - [x] 失敗時のロールバック（**部分的にインストールされた状態を残さない**）
  - [x] TTS が未セットアップなら Lumi は起動せず、`blocked` 画面に不足項目と解決方法を表示し、
    ユーザーが [終了] を選べる〔[ADR-034](decisions/ADR-034-gate-startup-on-complete-setup.md)〕
  - [x] ユーザーが別途インストール済みの AivisSpeech / VOICEVOX を検出する
- [x] **クレジット表示画面**（トレイ / `stage` の操作メニュー → クレジット）〔ADR-019 / 内容は [licensing.md](licensing.md) §6〕
  - [x] エンジン名・音源のクレジット例・ライセンス全文・禁止事項をユーザーが読める
  - [x] Phase 0 では Stage 側に静的に作る。`Provider.attribution()` との接続は Phase 1
  - [x] **Core が落ちていても読める**（クレジット画面は Core に接続しない）
  - [x] **推移的依存を含む OSS 通知の生成**〔2026-08-15。`scripts/generate-oss-notice.mjs` が3つの依存グラフから生成（284 件）。
    **GPL / AGPL を見つけたらビルドを失敗させる** → [licensing.md](licensing.md) §6〕
- [x] ハードコードされた「こんにちは」を AivisSpeech で発話 → リップシンク
  〔エンジンの起動・停止は Core が持つ（[architecture/core.md](architecture/core.md) §6）。
  リップシンクの方式は実測を経て確定（[interfaces/renderer.md](interfaces/renderer.md)）〕
- [x] **音声デバイスの選択と開通確認の骨格**〔duplex は使わないことにした → [ADR-020](decisions/ADR-020-split-audio-streams.md)。
  reference signal は Core が自前で持つ。リング・VAD・ミュートは Phase 1〕
- [x] **入出力が別デバイスのときの動作を実測** 〔2026-08-15。**別デバイスでも開ける。同一デバイスでも開けないことがある。**
  分離ストリームのドリフトは測定分解能以下 → [measurements/phase0.md](measurements/phase0.md)〕
- [x] **起動フェーズに応じた画面**〔2026-08-15。セットアップ → 取得中 → 起動中 → キャラクター表示。
  **出してよいかは Core が決める**（[architecture/ui.md](architecture/ui.md)「起動フェーズ」）〕
- [x] **ウィンドウの移動と拡大縮小**〔簡易版。キャラクターの上でドラッグ / ホイール。
  **位置と大きさは保存しない**（設定の保存形式が未確定のため。未確定事項 #9）〕
- [x] `PlatformShell` インターフェースを定義（Electron 退避路の確保）〔Stage に露出するのは OS 特権を含まない部分集合。[interfaces/shell.md](interfaces/shell.md)〕
- [x] **VRAM / RAM / インストーラサイズを実測して記録** 〔2026-08-15。**インストーラ 13.1 MB / Lumi 本体 497 MB / VRAM 55 MiB**。
  R1 解消 → [measurements/phase0.md](measurements/phase0.md)〕
- [x] **サイドカー同梱状態で sqlite-vec（SQLite ローダブル拡張）がロードできることを確認** 〔2026-08-15。`lumi-core.exe --self-check`〕
- [x] **Python サイドカーのパッケージング**〔PyInstaller の onedir。onefile は強制終了で %TEMP% に残骸を残す → [ADR-021](decisions/ADR-021-sidecar-packaging.md)〕

### 完了条件
インストーラを作って**別マシン**で起動し、**初回セットアップを通した上で**、キャラクターが立って一言喋る。

> **達成**〔2026-08-16〕。別マシンでインストーラから導入し、キャラクターが立って一言喋るところまで確認した
> → [measurements/phase0.md](measurements/phase0.md)「別マシンでの検証」。
> **ただし検証手順 15〜18（取得の経路）は別マシンでは未確認**であり、Phase 1 に持ち越す。
> Phase 1 では同じ経路に LLM / STT のセットアップが乗る（[ADR-023](decisions/ADR-023-llm-runtime-and-model-acquisition.md)）ため、まとめて回す。

> 初回セットアップの実装が Phase 0 を圧迫する場合は、**完了条件を「TTS は完全手動インストール前提」に緩め、取得フローを Phase 1 に送る**（[ADR-019](decisions/ADR-019-tts-engine-distribution.md) Alternative B への退避）。
> **ただしクレジット表示は落とさない。** 配布物ができた時点で義務が発生し、後から遡って直せないため。

### 検証手順
1. ~~`pnpm tauri build` で Windows インストーラが生成され、**サイズを記録**（R1判定）~~ 〔2026-08-15 完了。`pnpm build` で **13.1 MB**〕
2. クリーンな別マシン（または VM）にインストールして起動
3. キャラクターが透過背景で最前面に立ち、**背後のウィンドウが操作できる**
4. キャラクターの上にカーソルを乗せると**ホバーが検知される**（R2判定）
5. ~~タスクマネージャで Shell と Python Core の**両プロセスの RAM 使用量を記録**~~ 〔2026-08-15 完了。Shell 37 MB / Core 50 MB〕
6. 「こんにちは」を発話し、**口が動く**
7. Core プロセスを強制終了 → Shell が検知して再起動する
8. Shell を終了 → Core も確実に終了する（ゾンビプロセスが残らない）
9. **未知の `os.*` コマンドを Shell に送ると拒否され、ログに残る**（B3 骨格の確認）
10. ~~**アイドル時の VRAM / RAM 実測値を記録**~~ 〔2026-08-15 完了。VRAM 55 MiB〕
11. ~~音声ライブラリ利用規約を確認し、記録~~ 〔2026-08-15 完了 → [licensing.md](licensing.md)〕
12. ~~マイクとスピーカーが別デバイスの構成で duplex stream が開けるか確認~~
    〔2026-08-15 完了。**duplex を使わない**方針に変更 → [ADR-020](decisions/ADR-020-split-audio-streams.md)。
    別マシンでは `uv run python -m lumi.audio.probe` を回して**入出力が開通することだけ**確認する〕
13. ~~**サイドカーから sqlite-vec がロードできるか確認**~~ 〔2026-08-15 完了。別マシンでは `lumi-core.exe --self-check` を回す〕
14. ~~**インストーラに AivisSpeech / VOICEVOX のバイナリが含まれていないことを確認**~~ 〔2026-08-15 完了。92 ファイルを列挙して確認〕
15. **初回セットアップで「今は取得しない」を選び、「セットアップは完了していません」と不足項目が表示され、[終了] で終われることを確認**
    〔2026-08-15 **開発機で確認**（当時は「起動して TTS 未セットアップが出る」）。
    [ADR-034](decisions/ADR-034-gate-startup-on-complete-setup.md) で期待する結果が変わったので**取り直す** → Phase 1〕
16. **ネットワークを遮断した状態で取得を試み、明示的に失敗し、部分的な残骸が残らないことを確認**
    〔単体テストのみ。**実ネットワークでの断線試験はどちらのマシンでも未実施** → Phase 1〕
17. **ユーザーが選択するまで外部へのネットワークアクセスが発生しないことを確認**（パケットキャプチャ / Network-optional 原則）
    〔2026-08-15 **静的検査 + 経路で確認**（CI に入れた）。パケットキャプチャは未実施 → Phase 1〕
18. **クレジット画面にエンジン名・音源名・ライセンス全文が表示されることを確認**
    〔2026-08-15 **開発機で確認**。別マシンでは未確認 → Phase 1〕

### ここで失敗したら
Shell 選定（Tauri → Electron）とパッケージング方針を見直す。`PlatformShell` 抽象があるので Stage 側の実装は流用できる。

---

## Phase 1 — MVP: Talking Desktop Character

**目的: 「話しかけると答え、遮れば止まる」を成立させる。**

### 必須
- [x] Mic → Silero VAD → faster-whisper → Ollama(Qwen3系) → AivisSpeech → 再生 + **リップシンク** 〔Step E で結線。実機での通しは Step F〕
- [x] **音声の3層分離**（audio callback / VAD 専用スレッド / asyncio）。**コールバック内で推論しない** 〔Step D。AST で静的検査〕
- [x] **barge-in**（ミュート判定と発話区間確定を別閾値で。誤爆から復帰できること）〔Step D/E。実測は Step F〕
- [x] EchoGuard **L1**（適応的閾値。ヘッドホン前提を明示）〔Step D〕
- [x] Working Memory のみ（セッション内会話履歴。ベクトルなし）〔Step E〕
- [x] **Kernel 基盤** 〔Step A〕
  - [x] Attention Arbiter（`_foreground` 単一参照 / idle 必置 / `suspended`）
  - [x] `DeferredQueue`（TTL 付き）
  - [x] **`Job` と `inference_lease`**（Phase 2 の Reflection で必要になるが、後から入れると経路が変わる）
  - [x] Activity / Tool の独立した状態機械
  - [x] Cancellation 契約（cooperative / hard / non_cancellable）
  - [x] Command / EventBus（Signal ↔ DomainEvent 分離、stream_key 採番）
  - [x] Hook（固定セット）
  - [x] Crash Recovery の**イベント語彙**と `idempotency_key` の型（実装は Phase 4a）
- [x] **Permission Kernel の骨格**（L0 ツールのみ登録。`decide()` は本番と同じ関数。Kernel実行契約と Scope 正規化も本番と同じ経路）〔Step B〕
- [x] **Provenance の型と伝播**（L0 しか無くても型を後入れしない）〔Step B/E〕
  - [x] `block_trust` / `history_trust` / `session_trust`（sticky）の3スコープ
- [x] Provider interface（`load` / `unload` / `resource_hint` / **`attribution`** を含む）〔Step C〕
- [x] **推論スタックのセットアップ**〔[ADR-023](decisions/ADR-023-llm-runtime-and-model-acquisition.md) / [architecture/setup.md](architecture/setup.md) §2b〕
  - [x] **Ollama の検出**（取得もインストールもしない）と `model_missing` の判定 〔Step C / **Step F で実機確認**（[measurements/phase1.md](measurements/phase1.md)）〕 / [x] Stage への**提示**〔Step G〕
  - [x] **STT モデルの取得**（ピン留め + SHA-256 + ロールバック）。**ライブラリの自動ダウンロードを無効化する**〔Step F/G。同意フローに接続済み〕
  - [x] **Silero VAD を配布物に同梱** 〔Step D/E。faster-whisper 同梱の ONNX（MIT）を使う。**OSS 通知への Silero Team のクレジット追加は Step G で完了** → [licensing.md](licensing.md) §4.6〕
  - [x] 起動フェーズ（`boot`）を **LLM / STT / TTS の3つから導出**する〔Step G。
    **待機画面で待たせるのは「今まさに使えるようになる」ときだけ** → [architecture/setup.md](architecture/setup.md) §2b〕
  - [x] **3つが揃うまでキャラクターを出さない**（`blocked`）。不足項目と解決方法を出し、失敗には「再試行 / 今は取得しない」を出す
    〔[ADR-034](decisions/ADR-034-gate-startup-on-complete-setup.md)。~~「喋れるが聞けない」を正常な状態として出す~~ を取り消した〕
- [x] 構造化ログ（structlog）+ SLO 計測（p50/p95/p99、**`unaccounted_ms` を含む**）〔Step F で `turn_latency` / `vad.mute` を実装。**数値の記録はモデル取得後**〕
- [x] Inspector 最小版（Activity ツリー / レイテンシ内訳）〔Step G。**`stage` ウィンドウ内**。送信は EventBus 購読 → 別タスクで、barge-in の経路に載せない → [architecture/ui.md](architecture/ui.md) §5〕
- [x] **Content Pack の既定キャラクター**〔Step E で `content/characters/lumi/` を作成。**VRM 本体も配置済み**〔2026-08-19〕。`character.toml` の `[model]` が持つ〕

### Phase 0 からの持ち越し

- [x] **検証手順 15〜18 を別マシンで通す**（取得の経路。**LLM / STT のセットアップが同じ経路に乗った後**にまとめて回す）
  〔2026-08-22 通過。**Win11 仮想環境 / GPU なし。所要時間は記録していない** → [measurements/phase1.md](measurements/phase1.md)〕
- [x] **ネットワーク断線の実試験**（単体テストでは `.tmp-*` が残らないことを確認済み）
  - 〔2026-08-20〕**未実施のまま出た**: 実際の断線で `unexpected_error` が表示された。
    `httpx` の例外を理由に変換していなかったため → [architecture/setup.md](architecture/setup.md) §4「失敗は必ず『言える理由』になる」。
    単体テストは追加した（実例外を投げる）
  - 〔2026-08-22 通過〕**取得中にアダプタを切断し、理由が出ることと `.tmp-*` が残らないことを確認した。**
    **試したのは1パターンのみ**（接続確立前・DNS 失敗・断続的な切断は未試験） → [measurements/phase1.md](measurements/phase1.md)
- [x] **既定同梱 VRM モデル（光莉 / 作者: あわ）を `content/` に置き、配布物に含める経路を通す**（リポジトリにはコミットしない → [licensing.md](licensing.md) §4.5）〔2026-08-19。**Core が決め、Shell が配信し、Stage が描く** → [ADR-029](decisions/ADR-029-content-pack-asset-delivery.md)。PyInstaller spec が `model.vrm` の同梱を fail-closed で確認する〕
- [x] release ビルドでのカーソル監視 CPU 実測（debug では 1コア 2.8%）
  〔2026-08-22。**release では 1% 以下**（上限の観測。測定方法は記録していない） → [measurements/phase1.md](measurements/phase1.md)〕

### Stretch（詰まったら落とす）
- [x] 表情変化（`<|ACT|>` マーカー → ExpressionIntent → VRM ブレンドシェイプ）〔Step G。VRM に無い4つは Renderer 側で控えめに借りる → [interfaces/renderer.md](interfaces/renderer.md)〕

### 完了条件
話しかけると **p95 2.0 秒以内**に喋り始め、**途中で遮ると止まる**。

**〔2026-08-16 実測〕音声経路で p50 1.50 秒 / ウォーム p95 1.63 秒。レイテンシ条件を満たしている。**
ただし **GPU 構成での話**である（[ADR-025](decisions/ADR-025-tts-on-gpu.md)）。
CPU では TTS だけで 0.9 秒かかり届かない。→ [measurements/phase1.md](measurements/phase1.md)

**〔2026-08-22〕Phase 1 完了。** 実機での手動確認（マイクから話しかける / 喋っている途中で遮る）を実施し、
Phase 0 からの carry-over 3件（別マシンでのセットアップ検証 / ネットワーク断線の実試験 /
release ビルドのカーソル監視 CPU）もすべて閉じた。
上記のレイテンシ計測自体はオフライン注入（録音済み音声）で行ったものである。

> **carry-over 3件は「経路が通ること」の確認であって、分布や網羅の確認ではない。**
> 何を記録していないかは [measurements/phase1.md](measurements/phase1.md) の各「保証しないこと」にある。
> **別マシンの検証は GPU なしの仮想環境であり、そこでレイテンシ SLO は測っていない。**

### レイテンシ SLO

**表と区間別予算は [architecture/audio.md](architecture/audio.md) §7 が唯一の定義場所。**

要点: p50 < 1.5s / p95 < 2.0s / p99 < 3.0s。**区間合計は p50 目標の 85% 以下**に収め、残りを計測外処理の予備枠として空けておく。

✅ **設計上は決着済み〔2026-08-22。[ADR-039](decisions/ADR-039-speculative-stt.md)〕。実装と実測は Phase 2。**
Phase 1 の区間合計 1.27s（= p50 目標 1.50s の 85%）に記憶検索 0.05s を足すと 88% になる問題は、
**目標を動かさず、STT を VAD の無音待ちに重ねる**ことで解く（投機 STT）。
予算上のクリティカルパスは **1.10s / 73%** になり、予備枠が 15% → 27% に増える。
**これは予算の計算であって実測値ではない。** 実装と `critical_path_ms` / `stt_overlap_ms` の計測は
Phase 2 の「やること」にある。

### なぜ Kernel 基盤を MVP に入れるのか
**あとから Kernel を入れるのが一番危ない。** Attention Arbiter・Cancellation 契約・Provenance・Event 採番は、後から挿入すると全コードのシグネチャを書き換えることになる。L0 ツールしか無くても、**型と経路は本番と同じもの**を通す。

`Job` / `inference_lease` も同じ理由で Phase 1 に入れる。実際に使うのは Phase 2（Reflection）だが、後から入れると LLM 呼び出し経路が全部変わる。

### 落とすなら
表情から。**barge-in は絶対に落とさない**（後付けが最も難しく Arbiter 設計に影響し、かつ中核的差別化点）。

---

## Phase 2 — Memory

**目的: 「覚えていて、忘れて、思い出す」を成立させる。**

### ✅ 着手前に決めること — プライバシーとデータ保存〔2026-08-22 決着〕

**[contracts/privacy.md](contracts/privacy.md) を新設した**（決定は [ADR-038](decisions/ADR-038-privacy-and-data-retention.md)）。
**永続化されるものの一覧・保存先・暗号化・保持期間・消去対象は、すべてそこが唯一の定義場所である。**

要点だけ: **会話由来のデータを含む DB は保存時に暗号化し**（鍵は OS の秘密保管に預ける。ユーザーは管理しない）、
**保持期間は既定で期限あり・設定で無期限も選べる。「忘れる」と「消える」を混ぜない**——
記憶レコードは decay/archive の対象であって、保持期間の対象ではない。

⚠ **未検証: 暗号化 DB と sqlite-vec / FTS5 / PyInstaller の統合。**
下記「やること」の最初の項目。**ここが通らなければ ADR-038 を修正する新しい ADR を書く。黙って平文に落とさない。**

### 実装順 — 2a〜2g に分けた〔2026-08-22〕

Phase 2 は Phase 4 の次に大きい。分割の軸は「**単体で検証でき、次に進む前提を1つだけ確定させる単位**」。

| | 何を | なぜこの順か |
|---|---|---|
| **2a** | 暗号化ストレージ基盤（spike / DB 鍵 / `Database` / selfcheck） | **他の全部の前提。** ここが通らなければ ADR-038 を書き直すことになる |
| **2b** | 投機 STT + 計測 | **記憶検索を配線する前**（[ADR-039](decisions/ADR-039-speculative-stt.md)）。逆順だとその期間だけ予算の 88% で走る |
| **2c** | 記憶 DB のスキーマ / Episode 記録 / **保持期間ジョブと削除の記録** | **永続化を始める変更と、消す手段を同じ単位に入れる。** 消せないまま書き始めない |
| **2d** | MemoryStore（write / supersede / archive / purge / confirm）/ salience / 減衰 / 矛盾 | 検索の前に、**書かれるものの形**を確定させる |
| **2e** | Embedding Provider / `SqliteVecStore` / ハイブリッド検索 / プロンプト配線 | ここで初めて「思い出す」が成立する |
| **2f** | Reflection Job（LLM 抽出・`inference_lease`・provenance 伝播） | 検索が動いてから。**抽出の質は検索で確かめるしかない** |
| **2g** | 記憶 UI / 全消去 / エクスポート / マイク表示とミュート / Inspector | ユーザーが**見て直せる**ようになって Phase 2 が閉じる |

### やること

#### 2a — 暗号化ストレージ基盤〔2026-08-22 完了〕

- [x] 🔬 **spike: 暗号化 SQLite + sqlite-vec + FTS5 + PyInstaller**（**他の全部の前提**）
  〔4項目すべて通った。**+2.73 MiB / ADR-038 の修正は不要** → [ADR-040](decisions/ADR-040-encrypted-sqlite-driver.md) / [measurements/phase2.md](measurements/phase2.md)〕
- [x] **DB 鍵の生成と OS 秘密保管への保存**（Windows: DPAPI。**OS ごとの窓口として抽象化する**）
  〔`lumi/storage/secret.py`。**平文フォールバックは作らない**〕
- [x] マイグレーション基盤（`_schema_version`）を暗号化 DB の上に載せ直す
- [x] `--self-check` に「OS secret store」「Encrypted SQLite」を追加
  〔**dev で緑・サイドカーで赤**を実際に踏んだ。配布物でしか出ない失敗がある〕
- [x] **STT のデバッグ書き出しを撤去**（[contracts/privacy.md](contracts/privacy.md) §6 が録音経路を禁じている。
  Phase 1 の `lumi/audio/dump.py` は**ソースから実行すると既定で有効**だった → [architecture/audio.md](architecture/audio.md)）

#### 2b — 投機 STT

- [ ] **投機 STT**（VAD の無音待ちと STT を重ねる → [ADR-039](decisions/ADR-039-speculative-stt.md)）。
  不変スナップショット + 世代 ID + 原子的な照合。**曖昧なら採用しない**（fail-closed）。
  **single-flight + 1ターンあたりの上限**（STT の推論はキャンセルできないので、有界性は起動を絞って作る）。
  **`SILENCE_STARTED` イベントと非破壊スナップショットの追加**（現在は `SPEECH_ENDED` しか出ない）。
  **STT の実行経路は1本**（`SPEECH_ENDED` から直接呼ばない）
- [ ] `critical_path_ms` / `stt_speculative` / `stt_overlap_ms` / `stt_wait_ms` / `stt_discarded_ms` の計測
  （[architecture/audio.md](architecture/audio.md) §7）。**寄与は `stt_ms - stt_overlap_ms`。定数 0 で埋めない**
- [ ] 投機 STT の破棄率・`stt_overlap_ms`・`stt.speculation_capped` の発生率を実測して記録する（未確定事項 8f）

#### 2c — Episode と保持期間

- [ ] 記憶 DB のスキーマ（Episode / 記憶レコード / vec / FTS5）とマイグレーション
- [ ] Episode 記録（会話の生ログ）
- [ ] **イベント DB / 監査 DB をオンディスク（暗号化）にする**
- [ ] **保持期間の削除ジョブ**（Episode 90 日 / 監査ログ 180 日 / DomainEvent 30 日。
  **時刻を注入してテストできること**）と、無期限を選べる設定
- [ ] **削除の記録**（privacy.md §5。何を消したかは残し、**中身は残さない**）

#### 2d — 記憶コア

- [ ] **assertion_mode / evidence / provenance の実装**
- [ ] salience の決定論的補正（感情強度・新規性・明示・反復・参照回数）
- [ ] 減衰とアーカイブ（物理削除しない）
- [ ] 矛盾の supersede（`valid_from` / `superseded_by`）

#### 2e — 検索

- [ ] Embedding Provider（ONNX / CPU）
- [ ] ハイブリッド検索（vector + FTS5 + recency + salience + assertion_mode）+ トークン予算
- [ ] プロンプトへの配線（**assertion_mode ごとの提示のしかたを含む**）

#### 2f — Reflection Job

- [ ] **Reflection Job**（セッション終了時 / 長いアイドル時。**`Job` として実行し、`inference_lease` を取る**）

#### 2g — ユーザーが見て直せること

- [ ] **記憶の閲覧・編集・削除 UI**
- [ ] **「全部消して」**（privacy.md §2 の表の全行に到達すること。実行前に何が消えるかを見せる）
- [ ] **エクスポート**（可搬形式。**出力は平文になることを出力時に明示する**）
- [ ] アンインストーラの「覚えたことも削除する」（既定オフ）
- [ ] **マイクが開いていることの常時表示と、即座のミュート**

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
| 2 | ~~Python サイドカーのパッケージング（PyInstaller vs uv 同梱）~~ | **✓ 解消**〔2026-08-15 実測〕→ [ADR-021](decisions/ADR-021-sidecar-packaging.md)。**PyInstaller の onedir** |
| ~~3~~ | ~~Ollama を同梱するかユーザーに別途インストールさせるか~~ | **✓ 解消**〔2026-08-16〕→ [ADR-023](decisions/ADR-023-llm-runtime-and-model-acquisition.md)。**Ollama は検出のみ**（取得もしない）/ Silero VAD は同梱 / STT モデルは同意に基づく実行時取得 |
| 4 | ~~入出力が別デバイスのときの duplex stream の扱い~~ | **✓ 解消**〔2026-08-15 実測〕→ [ADR-020](decisions/ADR-020-split-audio-streams.md)。**duplex を使わない**（別ストリーム + Core が持つ reference） |
| 5 | **✓ 推奨モデルを Qwen 3.5 9B、軽量候補を 4B に固定。** 日本語会話品質と Tool Calling 品質の継続実測 | Phase 1（利用条件は [licensing.md](licensing.md) §4.8 に記録済み） |
| ~~6~~ | ~~🔴 **プライバシーとデータ保存の方針**~~ | **✓ 解消**〔2026-08-22〕→ [contracts/privacy.md](contracts/privacy.md), [ADR-038](decisions/ADR-038-privacy-and-data-retention.md)。**保存時の暗号化（DPAPI に預けたランダム鍵）/ 既定で期限のある保持期間 / 単一操作の全消去 / 生波形は永続化しない** |
| 6b | **暗号化 DB と sqlite-vec / FTS5 / PyInstaller の統合**（未検証。通らなければ ADR-038 を修正する ADR が要る） | **Phase 2 の最初に spike** |
| 7 | Embedding モデル（Ruri v3系 vs bge-m3）— 日本語検索品質 | Phase 2（実測） |
| ~~8~~ | ~~**DomainEvent の保持ポリシー**（`world:*` の高頻度ストリームが無限に貯まる）~~ | **✓ 解消**〔2026-08-22〕→ [contracts/privacy.md](contracts/privacy.md) §2。**既定 30 日 / 「全部消して」の対象**。Phase 3 まで持ち越さない |
| ~~8b~~ | ~~区間合計が p50 目標を超えている~~ | **✓ 解消**〔2026-08-18〕。`llm_first_token` を 537→**421 ms** に縮めたうえで、**p50 目標を 1.2s → 1.5s に置き直した**（1.27/1.50 = 85%）。`vad_ms` 0.43s はターンテイキングの方針で動かせず、旧目標と両立しなかったため。**p95 2.0s（完了条件）と区間別予算は据え置き** → [architecture/audio.md](architecture/audio.md) §7 |
| ~~8e~~ | ~~🔴 **記憶検索 0.05s を足すと 85% 規則を破る**~~ | **✓ 設計上は解消**〔2026-08-22〕→ [ADR-039](decisions/ADR-039-speculative-stt.md)。**目標を動かさず、STT を VAD の無音待ちに重ねる**（投機 STT）。予算上のクリティカルパス 1.27 → **1.10s / 73%**、予備枠 15% → 27%。**実装と実測は Phase 2**（8f） |
| 8f | **投機 STT の実測**（破棄率 / `stt_overlap_ms` / CPU 構成で隠れきらない分） | Phase 2（実装後） |
| ~~8c~~ | ~~**CPU TTS の固定費により p95 2.0 秒が達成できない**~~ | **✓ 解消**〔2026-08-16〕→ [ADR-025](decisions/ADR-025-tts-on-gpu.md)。**TTS と STT を GPU に載せた**。p50 1.50 秒 |
| ~~8d~~ | ~~🔴 **`vad_ms` の予算 0.18 秒が `min_silence_duration_ms`（400 ms）と矛盾する**~~ | **✓ 解消**〔2026-08-17〕→ [architecture/audio.md](architecture/audio.md) §7。**予算の側が誤り**。パラメータは 400 ms のまま（下げると文中の間で区間が切れる。実測済み）。**表には数値を書かず §5 を参照する**（同じ値を2箇所に書いたのが原因） |
| ~~9~~ | ~~設定の保存形式とスキーマ~~ | **✓ 解消**〔2026-08-17 / Step G〕→ [architecture/core.md](architecture/core.md) §6b。**JSON / `<data_dir>/settings.json`**。壊れたファイルは上書きしない・知らないキーは保持・環境変数の上書きは表示する。変更経路（Stage → Core の `request`）→ [ADR-028](decisions/ADR-028-stage-initiated-request.md) |
| ~~10~~ | ~~**`Activity.priority` の数値体系と `interruptible_by` を集合にする必要性**~~ | **✓ 解消**〔2026-08-16〕→ [ADR-024](decisions/ADR-024-activity-priority.md)。**`interruptible_at: int` の単一閾値**（`>=` で判定）。値は [architecture/agent.md](architecture/agent.md) §1 |
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
| R1 | **Python Core の同梱**。torch 依存だとインストーラ 1-2GB | 配布不能 | torch を避ける（CTranslate2 / ONNX） | ~~Phase 0~~ → **Phase 1 で再判定済み。インストーラ 65.7 MB**（[measurements/phase1.md](measurements/phase1.md)） |
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
