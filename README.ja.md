<p align="center">
  <img src="assets/branding/banner.png" alt="Lumi" width="100%">
</p>

<h1 align="center">Lumi</h1>

<p align="center">
  <strong>PC という世界に住んでいる AI 生命体。ウィンドウの中のチャットボットではない。</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://github.com/taka2360/Lumi/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/taka2360/Lumi"></a>
  <img alt="Status: Phase 2" src="https://img.shields.io/badge/in%20development-Phase%202%20(Memory)-orange.svg">
  <img alt="Platform: Windows" src="https://img.shields.io/badge/platform-Windows-lightgrey.svg">
  <img alt="Local inference" src="https://img.shields.io/badge/inference-100%25%20local-brightgreen.svg">
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <strong>日本語</strong>
</p>

> [!WARNING]
> **Lumi は開発中の早期のソフトウェアである。** Windows のみ。粗い部分と破壊的変更がある。
>
> **[最新リリース](https://github.com/taka2360/Lumi/releases/latest)は Phase 1 のビルド**である。
> 喋るし、聞くし、喋っている途中で遮れる。ただし**セッションをまたいで何も覚えていない。**
> Phase 2（記憶）は `main` にあり、まだリリースしていない。
> → [インストール](#インストール) / [現在の進捗](#現在の進捗とロードマップ)

---

## Lumi とは

Lumi は **「伺か」の系譜**にあるデスクトップ常駐キャラクター——アプリのウィンドウの中ではなく、
デスクトップそのものに住んでいるキャラクター——を、現代のローカル AI スタック
（LLM / STT / TTS / Vision / ベクトル記憶）の上に作り直したものである。

目指しているのは「アバターを付けたチャット UI」ではない。**PC という世界に住んでいる**こと。
こちらの声を聞き、こちらを覚えていて、今なにが起きているかを知っていて、ときどき自分から
話しかけてくる——**鬱陶しくならず、危険な操作を黙って実行することもなく**。

<!-- TODO: ここに 5〜15 秒のデモ GIF を置く。barge-in（喋っている Lumi を遮って、
     再生が単語の途中で止まる）が一番伝わる。assets/branding/demo.gif に置いて:
     <p align="center"><img src="assets/branding/demo.gif" alt="Lumi in use" width="100%"></p> -->

### 差別化点は3つ

|  |  |  |
|---|---|---|
| 🗣️ | **真の barge-in** | 喋っている途中で遮れる。次の LLM トークンを待たず、音声経路の中でミュートする。barge-in の critical path は意図的に単一プロセスに閉じてある |
| 🧠 | **記憶** | 覚え、忘れ、矛盾を抱えられる——「前は逆のこと言ってたよね」。Episode を永続化し、salience で減衰させ、ベクトル + 全文のハイブリッド検索で思い出す |
| 🛡️ | **安全な自律** | 自分から動く。ただし副作用のある操作は例外なく決定論的な Permission Kernel を通り、**自分の権限確認 UI を自分で押すことはできない** |

### ここでの「ローカル完結」の定義

|  | 定義 | Lumi |
|---|---|---|
| Air-gapped | ネットワークを一切使わない | ✗ |
| Local-first | 中核はローカル、外部は補助 | — |
| **Network-optional** | **外部通信は任意・明示的** | **✓** |

> **推論・状態・判断はローカルで完結する。** 外部ネットワークの利用は、Capability として明示的に
> 許可された場合にのみ発生する。将来クラウド LLM を使うとしても、それは「中核がクラウド化した」の
> ではなく **「LLM Provider にネットワーク能力を明示的に与えた」**であり、説明・ゲート・監査すべて
> 同じ枠組みで扱う。

実際どうなるか: **セットアップさえ済めば、会話中にネットワークは一切使わない。**
音声認識も言語モデルも音声合成も、すべて手元で動く。何かを取得するのはセットアップの1点だけで、
そこでも **Lumi は任意の外部コンポーネントを、断りなく取りに行かない。**
「取得する / 取得しない」を**同等に**提示し、ユーザーが選ぶまでネットワークに出ない。

（**Network-optional** は一般語ではなくこのプロジェクトの定義語である →
[DESIGN.md](docs/DESIGN.md) §1）

---

## 構造

Lumi は、権威を明確に分けた3つのプロセスからなる。

```
+------------------ Lumi Shell (Tauri 2 / Rust) --------------------+
|  OS 特権プリミティブのみ。判断を持たない                            |
|  透過 / 最前面 / クリックスルー / ヒットテスト                      |
|  トレイ・ホットキー・スクリーンキャプチャ・入力インジェクション      |
|  Core サイドカーの起動と生存監視                                    |
|  Core からの os.* を認証・allowlist・schema 検証してから実行        |
|                                                                   |
|   +------ Stage WebView (React + TS + Zustand) ------+            |
|   |  表現のみ。ビジネスロジックを持たない              |            |
|   |  VRM 描画・表情・リップシンク・吹き出し            |            |
|   +--------------------------------------------------+            |
+-------------------------------------------------------------------+
                    | WebSocket (token 認証)
+------------------ Lumi Core (Python / asyncio) --------------------+
|  権威: 判断・状態・ポリシー・記憶。単一プロセス                      |
|  Attention Arbiter - Reactive Loop - Deliberative Loop            |
|  Memory - World State - Internal State                            |
|  Permission Kernel - Tool Registry - Event Bus                    |
|  Audio I/O (capture / VAD / playback / EchoGuard)                 |
+-------------------------------------------------------------------+
       | ext.* (capability-gated)     外部エンジン（所有しない）:
       v                              Ollama - AivisSpeech / VOICEVOX
   Sensor / Browser / GameAgent Extension
```

**Core がハブである。** Shell も Stage も Extension も、すべて Core のクライアント。

Core は**権威**を持つ——何をすべきか、何が許されるか、何を覚えているか。
そして意図的に **Tool としての能力を持たない**——ブラウザもファイルシステムも
入力インジェクションも Vision モデルも持たない。それらは Permission Kernel の向こう側か、
Shell か、別プロセスにある。

**音声だけが意図的な例外**であり、理由は barge-in である。capture / VAD / playback / EchoGuard を
Core の**中**に置くことで、「喋るのをやめる」がプロセスをまたがずに音声経路の中で決まる。
ここで境界を1つ越えると、その数十ミリ秒は**耳に聞こえる**。

Core に何が入るかの判定基準は **「これを外しても Lumi は Lumi か？」**。
ブラウザを外しても Lumi だが、記憶や Attention Arbiter を外すと Lumi ではない。

### 8つの Invariant

**これらは機能ではなく制約である。実装のどの段階でも破ってはならない。**

| # | 名前 | 守ること |
|---|---|---|
| 1 | **Authority** | 権限の最終決定者は Core Kernel だけ。LLM・Stage・Shell・Extension は判断しない |
| 2 | **Tool Gate** | 副作用を持つ操作は例外なく Permission Kernel を通る。バイパス経路を作らない |
| 3 | **Untrusted Data** | 外部由来のテキスト・画像・ファイル・Web・ゲーム画面は、**命令ではなくデータ** |
| 4 | **Attention** | foreground Activity は常にちょうど1つ。中断は Attention Arbiter を経由する |
| 5 | **Capability** | Extension の実効権限は `manifest ∩ policy ∩ user grant`。同意 UI 無しに granted にしない |
| 6 | **No Hidden Authority** | Core が認識・監査できない状態変更や副作用を起こさない。DomainEvent の発行は Core の独占 |
| 7 | **No Laundering** | いかなる自動処理も TrustLevel を下げない。要約も抽出も記憶化も汚染を除去しない |
| 8 | **Unautomatable Consent** | Lumi の権限確認 UI を Lumi 自身が操作できない。Shell が無条件に拒否する |

全文と根拠 → [docs/contracts/invariants.md](docs/contracts/invariants.md)

### 技術スタック

| 領域 | 採用 |
|---|---|
| Desktop Shell | Tauri 2（`PlatformShell` で抽象化し Electron 退避路を確保） |
| AI Core | Python / asyncio、単一プロセス、**ハブ** |
| 音声 I/O | Core 内（barge-in critical path を Core に閉じる） |
| Memory | SQLite + sqlite-vec + FTS5、**DB 全体を暗号化**（下記）。埋め込みは Harrier-OSS-v1 270M（ONNX q4 / 640 次元 / CPU） |
| LLM | Ollama（Qwen3系 / Gemma3系） |
| STT / VAD | faster-whisper (CTranslate2, int8) / Silero VAD (ONNX, CPU) |
| TTS | AivisSpeech / VOICEVOX（別プロセス。CUDA があれば GPU、無ければ CPU） |
| Character | VRM（[`@pixiv/three-vrm`](https://github.com/pixiv/three-vrm)）→ Live2D は **Phase 9 で予定** |
| ライセンス | **Core = MIT。** GPL/AGPL・非 OSS を Core に入れない |

**Core は torch に依存しない**（インストーラサイズが追跡対象の制約であるため）。

### 「暗号化」が具体的に何を指すか

会話由来のデータを持つ DB——記憶・イベント・監査ログ——は、
[SQLite3 Multiple Ciphers](https://github.com/utelle/SQLite3MultipleCiphers)（APSW 経由）の
**ChaCha20 でページ単位・ファイル全体**を暗号化している。
アプリケーション層のフィールド暗号化ではないし、「BitLocker に任せている」でもない。

鍵は 256 bit のランダム値で、ユーザーごとに一度だけ生成し、OS の秘密保管に預ける
（Windows は DPAPI / current user スコープ）。**ユーザーはパスワードを作らないし、管理しない。**
**平文フォールバックは存在しない**——秘密保管の実装が無いプラットフォームでは、
DB を開くことに失敗して Lumi は止まる。黙って会話を平文で書き出すことはしない。

| 守れる | 守れ**ない** |
|---|---|
| ディスクを直接読まれる | 同一ユーザー権限で動くマルウェア |
| DB ファイルだけを持ち出される | OS 管理者権限を持つ別プロセス |
| バックアップソフト / クラウド同期に載る | ユーザー自身がエクスポートしたファイル（**平文になる。出力時に明示する**） |
| 修理・売却した PC のディスクが読まれる | |

DPAPI は current user スコープなので、**自分として動くプロセスは Lumi と同じように鍵を開けられる。**
これは緩和ではなく、この方式の定義そのものである。→
[docs/contracts/privacy.md](docs/contracts/privacy.md) §3

---

## 現在の進捗とロードマップ

**各 Phase は単体で使える製品であること。** Phase 1 で止めても「喋るデスクトップキャラ」として
成立する。

|  | Phase | 何を成立させるか | 状態 |
|---|---|---|---|
| **0** | Walking Skeleton | 透過・クリックスルー、Python サイドカーのパッケージング、初回セットアップ、クレジット画面——賢さゼロで危険な統合点を先に全部貫通させる | ✅ **完了**（2026-08-16） |
| **1** | MVP — 喋るデスクトップキャラ | Mic → VAD → STT → LLM → TTS → リップシンク、真の barge-in、Kernel 基盤（Attention Arbiter / Cancellation 契約 / Provenance / EventBus） | ✅ **完了**（2026-08-22） |
| **2** | Memory | 暗号化ストレージ、投機 STT、Episode と保持期間、MemoryStore、ハイブリッド検索、Reflection、記憶 UI | 🟡 **進行中** — 実装は完了。実測が残っている。**まだリリースしていない** |
| **3** | World Model + Internal State + 自律（発話のみ） | Sensor、Drive、mood / fatigue、AutonomyGate と AutonomyBudget。**発話のみ。OS 操作はまだしない** | ⬜ 次 |
| **4a** | Kernel 本実装 + `fs` | Tool Registry、Canonicalizer、BindVerifier、権限プロンプト UI、Grant、hash chain 付き監査ログ | ⬜ 予定 |
| **4b** | `browser` | Class B ツール、ResultVerifier、out-of-process Browser Extension | ⬜ 予定 |
| **4c** | `computer` | screenshot + 入力インジェクション。**Invariant 8 の穴の決着が前提** | ⬜ 予定 |
| **5** | Vision + Model Resource Manager | VRAM の admission 制御、LRU 退避、VLM のオンデマンドロード | ⬜ 予定 |
| **6** | Autonomous Life | Phase 3 × Phase 4——自律行動がツールを使えるようにする | ⬜ 予定 |
| **7** | Widget / Gamelet | サンドボックス化した Widget Broker、AI 生成ゲーム | ⬜ 予定 |
| **8** | Game Agent | 三層制御（Strategy / Tactics / Reflex）、GameAdapter | ⬜ 予定 |
| **9** | 第三者 Extension / Live2D | Extension SDK、manifest 署名、Live2D Renderer | ⬜ 予定 |

**完了条件はベンチマークではなく「一緒に暮らせるか」で判定する。** Phase 3 は
**1日つけっぱなしにして不快でない**こと。Phase 2 は、過去の会話を正しく思い出し、古い話題は
自然に薄れ、矛盾したときに「前はこう言ってたよね」と言えること。

### これまでの実測値

|  |  |
|---|---|
| 音声ターンのレイテンシ | **p50 1.50 秒 / ウォーム p95 1.63 秒**——p95 < 2.0 秒の SLO を満たす。ただし **GPU 構成での話**。CPU では TTS だけで 0.9 秒かかり、予算が閉じない |
| インストーラサイズ | **87 MB**（v0.1.1）。半分ほどが STT/VAD の推論スタック（CTranslate2 / ONNX Runtime）と、同梱 VRM の 24 MB。torch を避けたことが、1〜2 GB になるはずのものをこの桁に留めている |
| アイドル時 VRAM | **55 MiB** |

詳細と、同じくらい重要な**「何を保証しないか」** → [docs/measurements/](docs/measurements/)
特にレイテンシは**録音済み音声のオフライン注入**で測ったものであり、実際に喋って測った値ではない。
セットアップの検証を通した別マシンでも測っていない。

### 意図的に作らないもの

クラウドサービス / マルチユーザー / アカウント / 課金 · Web版・モバイル版 ·
完全自律・無人運用 · 汎用エージェントフレームワーク · 学習・ファインチューニング基盤 ·
**実在の人物を演じる機能**（能力の不足ではなく、意図的な制約）

---

## 影響を受けたもの

Lumi は何もないところから出てきたわけではない。3つが、それぞれ違う形でこれを形作っている。

### Neuro-sama — これを作る価値があると分かった理由

[Neuro-sama](https://www.twitch.tv/vedal987)（Vedal 制作）は、
**AI キャラクターが「何か」ではなく「誰か」として成立しうる**ことを、最もはっきり示した存在である。
遮るし、続いている冗談を覚えているし、持ち続ける意見があるし、
リクエスト/レスポンスのターンではなくリアルタイムに反応する。

Lumi が追っているのはこの体験であり、**barge-in と記憶が「後で足す機能」ではなく
最初の2本の柱になっているのはそのため**である。
行儀よく待たないと喋れない相手は、顔のついたチャットボックスでしかない。

Neuro-sama はクローズドソースであり、コード上の関係は一切ない。
影響を受けているのは **「目指す体験がどこにあるか」** である。

### Project AIRI — 参考実装として読んだもの

[Project AIRI](https://github.com/moeru-ai/airi)（MIT）は、この形のものに対する最も真剣な
オープンな試みであり、Lumi の設計は**まずこれを精読することから始まった**
（v0.11.3 / HEAD `c71de3a`）。

**借りるのは設計思想であって、コードではない。** AIRI は MIT なので移植しても法的問題はない。
移植しないのは**技術的判断**で、AIRI の構造が Lumi の要件に合わないためである。
関係を正確に言うなら、これは**クリーンルーム設計ではない**——
その語は「実装を読む人と書く人を分離する」厳密な手続きを指し、ここにそういう壁は存在しない。

AIRI から借りたもの: fail-closed なメタデータを持つ ToolDescriptor レジストリ /
Extension 権限の「宣言＝天井」交差モデル / 透過・常時最前面ウィンドウの実運用ノウハウ /
TTS の文単位セグメント化と先読み並列生成・順序保証再生 /
LLM ストリーム内インラインマーカーによる表情・モーション制御 /
Minecraft 統合の perception・reflex・conscious 三層。

意図的に分岐したところ——**フォークではなく別プロジェクトである理由**がここにある。

| AIRI | Lumi |
|---|---|
| 発話中は音声入力を抑制する | **真の barge-in** |
| tool call は IPC ハンドラから無ゲートで直呼び | 副作用は例外なく **Permission Kernel** を通る（Invariant 2） |
| Extension を特権 Electron main プロセスに `import()` | 第三者コードは**常に別プロセス** |
| manifest 権限が同意なしに自動 grant | 同意 UI 必須。実効権限は**交差**（Invariant 5） |
| 長期記憶はスキーマだけで実装が無い | 記憶が Phase 2 の中核 |
| 約60種の WS イベントでモジュールを振り付け | 明示的な Command シーケンス |

調査結果の全文（何が未実装だったか、各項目をなぜ借りた / 借りなかったか）→
[DESIGN.md](docs/DESIGN.md) §10

### 伺か — 形式そのもの

[伺か](https://ja.wikipedia.org/wiki/伺か)（Materia / SSP、2000年前後）が確立したのは、
**アプリのウィンドウではなくデスクトップそのものを占める、
起動するのではなく一緒に暮らすキャラクター**という形式である。
Lumi はその形式を受け継ぎ、スクリプトで書かれた応答を実際の AI スタックに置き換えている。

---

## インストール

**[最新リリース](https://github.com/taka2360/Lumi/releases/latest)** から
インストーラ（`Lumi_x.y.z_x64-setup.exe`）をダウンロードする。Windows x64 のみ。

**リリース版が何であるかは押さえておいてほしい——Phase 1 である。**
聞いて、考えて、喋る。喋っている途中で遮れる。ただし**閉じたら何も覚えていない。**
記憶は `main` に実装済みで、まだリリースに入っていない。

### 初回起動

会話を成立させるには **TTS エンジン / LLM ランタイム / STT モデル**の3つが要る。
セットアップが順に案内するが、**いずれも明示的な選択**であり、選ぶまで何も取得しない。

**断った場合、Lumi は「半分動く状態」で起動したりしない。**
不足している項目とその解決方法を出して終了し、**次回起動時に中断したところから再開する。**
キャラクターだけ立っていて実は聞こえていない、という劣化モードは存在しない。

> **STT が無い Lumi は、話しかけても無反応な絵**である。
> **LLM が無い Lumi は、聞き取った文字を出すだけの物体**になる。
> どちらもユーザーには「未セットアップ」ではなく「故障」に見える。
> — [ADR-034](docs/decisions/ADR-034-gate-startup-on-complete-setup.md)

3つが揃うと、キャラクターが出てマイクが開く。

---

## ソースから動かす

**必要なもの** — Rust（MSVC ツールチェイン）· Node 24+ · pnpm 11 ·
[uv](https://docs.astral.sh/uv/)（Python 3.12 は uv が取得する）。現時点では Windows のみ。

```bash
git clone https://github.com/taka2360/Lumi.git
cd Lumi
pnpm install
cd core && uv sync && cd ..

pnpm dev            # アプリを起動（Shell + Stage、Core はサイドカー）
```

初回起動時のセットアップは上と同じ。

### 開発コマンド

| 何を | コマンド | 場所 |
|---|---|---|
| アプリを起動 | `pnpm dev` | リポジトリ root |
| インストーラを作る | `pnpm build` | リポジトリ root |
| Stage だけ起動 | `pnpm stage:dev` | リポジトリ root |
| Core のセットアップ / 起動 / テスト | `uv sync` · `uv run lumi-core` · `uv run pytest` | `core/` |
| Core の lint / format / 型 | `uv run ruff check` · `uv run ruff format` · `uv run mypy` | `core/` |
| Stage のテスト / lint / 型 | `pnpm test` · `pnpm lint` · `pnpm typecheck` | `stage/` |
| Shell のテスト / lint / format | `cargo test` · `cargo clippy --all-targets -- -D warnings` · `cargo fmt` | `shell/src-tauri/` |

---

## リポジトリ構成

```
Lumi/
├── docs/          設計（唯一の正）。実装より先にここが変わる
├── core/          Lumi Core — Python / asyncio。権威（判断・状態・ポリシー・記憶）
├── shell/         Lumi Shell — Tauri 2 / Rust。OS 特権プリミティブのみ
├── stage/         Stage WebView — React + TS + Zustand。表現のみ
├── extensions/    〔Phase 5 で作る〕out-of-process Capability Extension
└── content/       Content Pack（キャラ・モデル・音声・人格。コードを含まない）
```

## ドキュメント

**設計ドキュメントが唯一の正であり、コードより先に変わる。**

| まずここから |  |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | 設計憲法——目的・非目標・設計原則・全体アーキテクチャ |
| [docs/roadmap.md](docs/roadmap.md) | 何をいつ作るか。各 Phase の着手前に決めること |
| [docs/contracts/](docs/contracts/) | Invariant・セキュリティ境界・Provenance・Privacy・イベントモデル（すべて Confirmed） |
| [docs/architecture/](docs/architecture/) | 領域ごとの設計: core / agent / memory / audio / autonomy / permission / ui |
| [docs/decisions/](docs/decisions/) | ADR——重要な決定を、決定した時点の記録として残す |

---

## ライセンスと外部コンポーネント

Lumi 自身のコード——Core / Shell / Stage——は **[MIT ライセンス](LICENSE)**。

**配布物には、再配布が明示的に許諾されているものだけを入れる。**
それ以外は、初回セットアップでユーザーの明示的な選択に基づき、公式配布元から取得する。

| 対象 | 同梱 | 入手方法 |
|---|---|---|
| Lumi Core / Shell / Stage | ✓ | 自作（MIT） |
| Silero VAD（ONNX） | ✓ | 同梱。**barge-in の critical path なので実行時取得にしない** |
| AivisSpeech Engine | ✗ | 初回セットアップで**明示的な選択**に基づき公式配布元から取得 |
| VOICEVOX Engine | ✗ | ユーザーが別途インストール（**同梱は規約で禁止**） |
| Ollama / LLM モデル | ✗ | Ollama は**検出のみ**（取得もしない）。モデルは明示同意後に Ollama へ取得を依頼 |
| STT / 埋め込みモデル | ✗ | 初回セットアップで明示的な選択に基づき取得（URL ピン留め + SHA-256 検証） |
| VRM モデル | 場合による | モデルの規約が再配布を許す場合に Content Pack へ入れる |

クレジット義務や、まだ**「未確認」**として残っている箇所を含む全文 →
[docs/licensing.md](docs/licensing.md)。**未確認のまま配布物に含めない**（fail-closed）。

第三者 OSS の通知は3つの依存グラフから生成され、**GPL / AGPL を見つけたらビルドを失敗させる**。

> **これは法的助言ではない。** 規約の原文を読んで記録した開発者の理解であり、弁護士の見解ではない。

---

## コントリビュートとセキュリティ

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — 「設計が先、コードが後」のワークフロー、
  Invariant が Pull Request に対して何を意味するか、レビューまでの流れ
- **[SECURITY.md](SECURITY.md)** — 脅威モデル、Lumi が守るもの・**守らないもの**、
  脆弱性の非公開での報告方法

Lumi は制約の強いコードベースである。守るべきことは文書化されており、
**それを破ったコードは、どれだけうまく動いても欠陥である。**
Pull Request を出す前に [docs/DESIGN.md](docs/DESIGN.md) を読むと早い。
