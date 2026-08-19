# Core Architecture

> Core の定義、プロセス構成、通信の namespace、依存関係。

親: [DESIGN.md](../DESIGN.md) / 関連: [../contracts/authority-matrix.md](../contracts/authority-matrix.md)

---

## 1. Core の定義

> **Core は権威を持つが、能力の実装を持たない。**

「Core を小さくする」という表現は誤解を招く。Core は最終的にコード量としては大きくなる。正しい定義は上記である。

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

### 分類の判定基準

> **「これを外しても Lumi は Lumi か？」**

| コンポーネント | 外したら | 分類 |
|---|---|---|
| ブラウザ操作 | Lumi のまま | Extension |
| 記憶 | Lumi ではない | Core |
| Attention Arbiter | Lumi ではない | Core |
| Minecraft を遊ぶ能力 | Lumi のまま | Extension |
| Permission Kernel | Lumi ではない（危険な別物になる） | Core |
| VRM 描画 | Lumi ではない（ただし Live2D でもよい） | Core が抽象を持ち、実装は Renderer |
| Drive System | Lumi ではない（ただのチャットボットになる） | Core |

---

## 2. プロセス構成

```
┌──────────────────────── Lumi Shell (Tauri 2 / Rust) ─────────────────────────┐
│  透過/最前面/クリックスルー/ヒットテスト・トレイ・ホットキー                     │
│  スクリーンキャプチャ・入力インジェクション・Coreサイドカーの起動と生存監視       │
│  os.* 要求の認証・schema検証・allowlist検査（B3）                              │
│   ┌────────── Stage WebView (React + TS + Zustand) ──────────┐               │
│   │  VRM描画・表情/モーション/リップシンク・吹き出し            │ ← Tauri IPC   │
│   │  Widgetホスト + Widget Broker・設定UI・権限プロンプト      │   shell.*     │
│   └──────────────────────────────────────────────────────────┘               │
└──────────────────────────────────────────────────────────────────────────────┘
        │ os.* (WS)                                    │ stage.* (WS)
        ▼                                              ▼
┌────────────────────── Lumi Core (Python / asyncio) ──────────────────────────┐
│  Attention Arbiter / Reactive Loop / Deliberative Loop                        │
│  Memory / World State / Internal State / Permission Kernel                    │
│  Tool Registry / Event Bus / Audio I/O / Extension Host / Provider Registry   │
└────────────┬──────────────────────────────────────────────────────────────────┘
             │ ext.* (WS / stdio)
   ┌─────────┼─────────┬──────────────┐
   ▼                   ▼              ▼
 Sensor Ext      Browser Ext     GameAgent Ext ...

 外部エンジン（別プロセス / 所有しない）: Ollama │ AivisSpeech / VOICEVOX
```

### なぜ Core が単一の Python プロセスなのか

| 選択肢 | 評価 |
|---|---|
| **Python 単一プロセス** ✓ | VAD / STT / Embedding / TTS クライアントの生態系が Python に集中。プロセス間で音声を運ぶ必要がない |
| Rust Core | 生態系が薄い。faster-whisper / Silero / ONNX の Python バインディングを再実装することになる |
| TypeScript Core | AIRI の選択。ブラウザ生態系との親和性は高いが、音声処理を worklet/worker で組む必要があり barge-in の critical path が長くなる |
| Python を複数プロセスに分割 | 音声・記憶・Agent を分けると IPC が増え、barge-in のレイテンシが読めなくなる |

→ [ADR-002](../decisions/ADR-002-python-core-as-hub.md)

### Core がハブである

**Shell も Stage も Extension も Core のクライアント。**

AIRI は eventa IPC（Electron main↔renderer）と WebSocket（server-runtime）の2系統が絡み合っており、「この通信はどっち経由か」が追いにくい。Lumi は Core をハブに固定する。

---

## 3. 通信の namespace

| namespace | 経路 | 内容 | 例 |
|---|---|---|---|
| `shell.*` | Tauri IPC (Shell ↔ Stage) | ウィンドウ自身の見た目と入力。**1ms 以下であるべきもの** | `shell.window.set_clickthrough`, `shell.window.drag_start`, `shell.hover.state` |
| `stage.*` | WS (Core → Stage) | Lumi の表現と状態 | `stage.character.speak`, `stage.character.expression`, `stage.widget.open`, `stage.bubble.show` |
| `os.*` | WS (Core → Shell) | OS 特権操作の依頼 | `os.capture.screenshot`, `os.input.click`, `os.window.create` |
| `ext.*` | WS/stdio (Core ↔ Extension) | Tool 呼び出し、Sensor push | `ext.tool.invoke`, `ext.sensor.push` |

### 規則

> **`shell.*` は絶対に AI の判断を運ばない。**
> **`stage.*` は絶対に OS 特権を要求しない。**

この2つを守れば経路の混乱は起きない。

### なぜ Stage が2経路を持つのか

Stage は Shell（Tauri IPC）と Core（WS）の両方と話す。これは意図的である。

- ウィンドウのドラッグやクリックスルー切替は**フレーム単位の応答性**が要る。Python を往復させられない
- キャラクターの発話や表情は**Lumi の判断**であり、Core が決める

namespace を分けることで、「どちらの経路を使うべきか」が名前から自明になる。

---

## 4. 依存関係

```
Stage ──→ Core ←── Shell
              ↑
         Extension

Core 内部:
  AttentionArbiter ──→ (ReactiveLoop | DeliberativeLoop)
  Reactive/Deliberative ──→ Memory, World, Internal, ProviderRegistry, ToolRegistry
  ToolRegistry ──→ PermissionKernel ──→ (Canonicalizer, Policy, GrantStore, AuditLog)
  すべて ──→ EventBus（発行のみ）
```

### 逆向きの依存を作らない

| 禁止 | 理由 |
|---|---|
| `PermissionKernel` → `Tool` | Kernel が個別ツールを知ると、ツール追加のたびに Kernel が変わる |
| `Memory` → `Agent` | 記憶はエージェントの都合を知らない。検索クエリを受け取るだけ |
| `EventBus` → 何か | Bus は誰も知らない。誰でも publish/subscribe できる |
| `World` → `Sensor` | Core は Sensor の実装を知らない。Signal を受け取るだけ |
| `Core` → `Shell` の具体実装 | `PlatformShell` interface 越しにのみ話す |

### モジュール構成

```
core/lumi/
├── provenance.py    ProvenanceClass / TrustLevel / join / propagate（**依存ゼロ**）
├── kernel/          arbiter, activity, job, command, event, hooks,
│                    cancellation, inference_lease, recovery, scheduler
├── agent/           reactive, deliberative, drives,
│                    session（Working Memory + sticky session_trust）,
│                    prompt（PromptAssembly）, markers（<|ACT|>）,
│                    sentences（文分割）, speech（PlaybackScheduler → audio.md §6）,
│                    runtime（会話の組み立て。**判断を持たない**）
├── memory/          working, episodic, semantic, reflection, retrieval, decay
├── world/           facets, snapshot, projection
├── internal/        mood, fatigue, drives state
├── permission/      policy, canonicalization, bind_verifier, result_verifier,
│                    grants, audit
├── tools/           registry, descriptors,
│                    builtin/        fs, computer, memory, character（Class A）
├── providers/       llm/ stt/ tts/ embedding/ vision/
├── audio/           ring, resample, capture, vad, playback, io（EchoGuard L1 は vad 内）
├── extensions/      host, manifest, protocol
├── storage/         sqlite, migrations, vector store
├── content/         Content Pack の**読み取り専用ローダ**（extension.md §9）
└── transport/       ws server, protocol schema
```

**`content/` はローダであって Content Pack ではない。** パックの実体はリポジトリ root の
`content/characters/<name>/` に置かれ、**データのみでコードを含まない**（[extension.md](extension.md) §9）。
Core 側にローダが要るのは、`[credit]` の欠落を **fail-closed で落とす**責任が Core にあるため。

**`kernel/` が他のどのモジュールにも依存しないこと**を静的検査で保証する。kernel は型と調停だけを持ち、具体的な能力を知らない。

### なぜ `provenance.py` がトップレベルにあるのか〔Phase 1〕

**`kernel/` より下に置く必要があるため。** `Signal` が `trust_level` を持つ
（[../contracts/event-model.md](../contracts/event-model.md)）ので、kernel は provenance に依存する。
memory/ の下に置くと **kernel → memory の依存**が生まれ、上の規則が破れる。

trust の型は kernel・permission・tools・agent・memory の**すべてが使う横断的な制約**
（[Invariant 3](../contracts/invariants.md) / 7 の型による実装）であり、どれか1つの下に置くと
「そのモジュールの持ち物」に見えてしまう。**依存ゼロの単独モジュールにして、位置そのもので
「これは全体の制約である」と示す。**

規則の定義は [../contracts/provenance.md](../contracts/provenance.md)。

**`kernel/` が import してよい lumi 配下のモジュールは、`lumi.kernel.*` / `lumi.provenance` /
`lumi.logging` の3つだけ**（構造化ログは能力ではなく全モジュールの土台）。
永続化のような「外の世界」は Protocol（`EventStore`）で受け取り、実装は kernel の外に置く。
**これを AST の静的検査で縛る**（`core/tests/test_kernel_boundaries.py`）。

---

## 5. Shell と Stage の責務

**責務表・Tauri 2 固有の課題・AIRI から借りる運用知見は [ui.md](ui.md) が唯一の定義場所。**

Core から見た要点だけ:

| | 一行で |
|---|---|
| **Shell** | OS 特権プリミティブのみ。判断を持たない（Invariant 8 の拒否を除く）。`os.*` の検証層を持つ（B3） |
| **Stage** | 表現のみ。ビジネスロジックを持たない。**ロジックは Core にのみ存在する** |

判定基準: **Stage のストアから読める値は、すべて Core が `stage.*` で配信したものであるべき。** Stage が自分で計算して状態を作っていたら、それはロジックが漏れている。

→ [ui.md](ui.md), [ADR-001](../decisions/ADR-001-desktop-shell-tauri.md), [../interfaces/shell.md](../interfaces/shell.md)

---

## 6. 外部エンジン

Lumi が**所有しない**プロセス。

| エンジン | 用途 | 起動 |
|---|---|---|
| Ollama | LLM | ユーザーが別途インストール（同梱可否は未確定事項 #3。[ADR-019](../decisions/ADR-019-tts-engine-distribution.md) と同じ論法を適用予定） |
| AivisSpeech | TTS | **配布物に含めない。** 初回セットアップでユーザーの明示的な選択に基づき公式配布元から取得 |
| VOICEVOX | TTS（代替） | **配布物に含めない**（規約が無断再配布を禁止）。ユーザーが別途インストールしたものを検出 |

配布方針の全体 → [../licensing.md](../licensing.md) / 決定 → [ADR-019](../decisions/ADR-019-tts-engine-distribution.md)

### 扱い

- 127.0.0.1 bind（外部からアクセスできない）
- **出力は必ず `untrusted`**（[../contracts/provenance.md](../contracts/provenance.md)）
- 起動していない場合は明示的なエラーにする。黙って劣化しない
- **セットアップされていない場合と、起動に失敗した場合を区別する。** 前者は「まだ導入されていない」、後者は「壊れている」であり、ユーザーに要求する行動が違う
- `Provider` interface 越しにのみアクセスする（差し替え可能性の担保）

### 所有と生存〔Phase 0 で確定〕

**エンジンのプロセスを起動するのは Core。** Shell ではない（`os.*` の allowlist に
プロセス起動を足すと、B3 の攻撃面が Core 侵害時に広がる → [../contracts/security-boundaries.md](../contracts/security-boundaries.md)）。

| 規則 | 理由 |
|---|---|
| **すでに動いているエンジンには触らない**（使うだけ） | ユーザーが自分で起動したものかもしれない。Lumi の終了に巻き込んで落とすのは越権 |
| **Lumi が起動したものだけ Lumi が止める** | 上の裏返し。所有していないプロセスを kill しない |
| 起動待ちに**上限を設ける**。超えたら失敗として出す | 初回起動はエンジン自身のモデル取得で数分かかる。無限に待つと「起動しない」と区別できない |
| **`installed` / `detected`（導入の状態）と `stopped` / `starting` / `ready` / `failed`（プロセスの状態）を別の軸にする** | 「入っているが起動に失敗した」を表現できないと、ユーザーへの案内が嘘になる |

> **ゾンビ対策を二重に持たない。** Core が起動した子プロセスは、Shell が Core に付けた
> Job Object（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）を継承する。Shell が強制終了されても
> エンジンごと落ちる。**これは実測で確認する**（[../measurements/phase0.md](../measurements/phase0.md)）。
> 他 OS では別の手段が要る。

---

## 6b. 設定の保存〔Phase 1 Step G。未確定事項 #9 の決着〕

> **このファイルが唯一の定義場所である**（保存形式・スキーマ・優先順位）。実装 → `core/lumi/settings.py`

**設定は Core が持つ。Stage は表示して依頼するだけ**（[ui.md](ui.md) §2）。

| | |
|---|---|
| 置き場所 | `<data_dir>/settings.json` |
| 形式 | **JSON** |
| スキーマ版 | `version`（**意味が変わったときだけ上げる**。項目の追加は既定値で吸収されるので上げない） |
| 何が設定項目か | **`lumi.settings.KEYS` が唯一の定義**（キー / 環境変数 / 既定値） |

### なぜ JSON か — Content Pack は TOML なのに

**書く主体が違う。** Content Pack は**人が書く**のでコメントの書ける TOML。
設定は**プログラムが書く**（設定 UI から）。`tomllib` は読み取り専用なので、
TOML で書くにはキー数個のために依存を1つ増やすことになり、
そもそも**プログラムが書き直すファイルでコメントは残らない。**

### 形式より重要な4つの規則

| 規則 | 理由 |
|---|---|
| 壊れたファイルは**既定値に落ちるが、絶対に上書きしない** | 上書きは「ユーザーが意図したこと」の唯一の写しを壊す。手で直せる状態を残す |
| 知らないキーは**保存時に保持する** | 新しい Lumi の設定を古い Lumi で開いても消えない |
| 不正な値は**そのキーだけが既定に落ちる** | 誤字1つでファイル全体を捨てると、他の設定まで失われる |
| 環境変数はファイルを**上書きするが、それを表示する** | 黙って上書きすると「変えたのに効かない」が説明不能になる |

**環境変数による上書きはファイルに書き戻さない。** 一時的な逃げ道が黙って恒久化する。

### 書き込みは原子的に

丸ごと書いて rename する。**途中まで書けたファイルは次回起動で「壊れている」と判定され、
上の1つ目の規則により以後ずっと保存できなくなる。**

### 変更経路 — Stage → Core の `request`〔[ADR-028](../decisions/ADR-028-stage-initiated-request.md)〕

設定を変えるには **Stage → Core の要求方向**が要る。Phase 0 の WS はクライアントから
`hello` と `result` しか受理しておらず、**設定 UI が原理的に作れなかった。**

```
Stage → Core:  kind = "request"   （Stage は問う）
Core → Stage:  kind = "result"    （Core が決めて答える）
```

**`command` は依然としてクライアントから受理しない。** Core が決め、クライアントは問う
—— この非対称が、経路が存在するようになった後も Invariant 1 を保つものである。

| 受理の条件 | 実装 |
|---|---|
| `stage.*` namespace であること | `method_matches_role` |
| **Core が明示的に登録した method** | `WsServer.on_request` のレジストリ。**未登録は `unknown_method`** |
| payload が正しいこと | **各ハンドラの責任**（transport は検証しない） |

**登録レジストリが allowlist そのものである。** 書かれていない経路は存在しない。

Phase 1 で登録されているのは **`stage.settings.update` の1つだけ**。
一覧は [../contracts/wire.json](../contracts/wire.json) の `inbound_methods` が持ち、
**3言語のテストで固定している。**

**この経路に Tool 実行を載せるときは、必ず新しい ADR を書く**（Invariant 2）。

### モデル設定は次回起動から、表示言語は即時に効く

**動作中のモデルを差し替えない。** ターンの途中でモデルが入れ替わるのは
Phase 5 の `ModelResourceManager` の仕事であり、**今それを装うと表示値が嘘になる。**
UI はそう明記する。ただし `locale` は推論状態を変えない表示設定なので、保存成功後に
`stage.settings.state` を受けた Stage が即時反映する。値は `auto` / `ja` / `en` のみを受理し、
`auto` は OS / WebView の優先言語を使う。不正な値は Core が拒否する。

---

## 7. 起動シーケンス

```
1. Shell 起動
2. WS token を生成
3. Python Core をサイドカーとして起動（token を環境変数で渡す）
4. Core が 127.0.0.1 で WS listen
5. Shell が WS 接続 → token 認証
6. Core が storage を open、マイグレーション適用
7. Core が Provider を登録（load はまだしない）
8. Core が Extension manifest を読み、有効なものをロード
9. Core が Attention Arbiter を初期化し、idle Activity を running で生成
10. Core が ready を Shell に通知
11. Shell が Stage ウィンドウを作成
12. Stage が WS 接続 → token 認証
13. Core が Stage に初期状態を配信（character, world 投影）
14. Core が Audio I/O を開始（VAD 待機）
15. Core が TTS エンジンのプロセスを起動し、プロセス状態を配信（`starting` → `ready` / `failed`）
16. Core が LLM / TTS / STT の3つを**ウォームする**（重み・話者・モデルを実際に載せる）
```

**7 の「load はまだしない」の例外は TTS だけ。** 起動フェーズ `starting` は
「エンジンのプロセスを起動中」を表すので（[ui.md](ui.md)「起動フェーズ」）、
**誰も起動しなければ Stage は永久にローディングのままになる**（実際に起きた）。
最初の発話まで遅らせると、エンジンの起動時間（初回は数分）が最初の返事に乗る。

**15 は 14 を待たせない。** 聞くことはエンジンに依存しないので、
起動を待つ間もマイクは開いている。プロセス状態を配るのは、起動した本人（Core）の責任。

**16 は「7 の load をここでやる」ではなく、「load したと言えるところまでやる」である。**
接続確認だけで返る `load()` はコストを最初のターンに先送りしているだけで、実際に
**最初の返事が 7.5 秒かかった**（2026-08-18 実測）。内訳と規則 →
[../interfaces/provider.md](../interfaces/provider.md)「`load()` は接続確認ではない」

**16 も 14 を待たせない。** 3つを順に温めるので十数秒かかるが、その間もマイクは開いている。
**温まる前に話しかけられたら、そのターンが待つ**（`ProviderRegistry` が kind ごとに直列化するので、
二重にロードはしない）。**「まだ温まっていないので聞きません」にはしない。**

### 障害時

| 障害 | 対応 |
|---|---|
| Core が落ちた | Shell が検知して再起動。Crash Recovery が走る（Phase 4a） |
| Shell が落ちた | Core も終了（サイドカーとして親に紐づく）。ゾンビを残さない |
| Stage が落ちた | Shell が再作成。Core は動き続ける |
| Extension が落ちた | Core が検知して該当 capability を無効化。Lumi は動き続ける |
| 外部エンジンが落ちた | Provider がエラーを返す。ユーザーに明示的に伝える |

**Phase 0 の検証項目**: Core 強制終了 → 再起動、Shell 終了 → Core も終了（ゾンビなし）。
