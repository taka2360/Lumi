# Extension Architecture

> **低レイテンシ要件とサンドボックス要件は両立しない。だから機構を2つに分ける。**

親: [DESIGN.md](../DESIGN.md) / 関連: [../interfaces/extension.md](../interfaces/extension.md), [../interfaces/provider.md](../interfaces/provider.md), [ADR-005](../decisions/ADR-005-extension-two-mechanisms.md)

---

## 1. 3層

```
Core       権威（Decision / State / Policy / Scheduling / Coordination / Memory）
Extension  能力
Content    キャラクター・モデル・音声・人格・ゲーム資産（コード無し、データのみ）
```

**Core を過剰に plugin 化しない。** 交換したいもの・追加したいものだけを Extension にする。

---

## 2. Extension を2機構に分ける（+ Stage 実行の Renderer）

**中核は2機構。** これが `runtime` フィールドで区別される。

| | **Provider Extension** | **Capability Extension** |
|---|---|---|
| `runtime` | `in-core` | `out-of-process` |
| 例 | LLM / STT / TTS / Embedding / Vision | Browser / GameAgent / Sensor / Widget |
| 実行 | **Core 内**（Python entry point） | **別プロセス**（WS / stdio、任意言語） |
| 信頼 | **`official` 必須** | **untrusted。capability-gated** |
| 理由 | ホットパス。プロセス跨ぎが許容できない | 第三者コード。隔離が必須 |
| 権限 | Core と同じ（プロセス権限をそのまま持つ） | manifest ∩ policy ∩ grant |
| Tool | — | **Class B の lane のみ**（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)） |

### Extension にしないもの — `fs` / `computer`

**`fs.*` と `computer.*` は in-core built-in Tool である。Extension ではない。**

理由は2つ。

1. **`BindVerifier` が成立するのは Handle が Core のプロセス内にある場合だけ**（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)）。out-of-process にすると、Kernel 実行契約の要である事前検証が失われる
2. これらは Lumi 自身が書く公式実装であり、**第三者コードではない**。隔離の動機が無いのに、隔離のコストとして最も重要な防御を失っていた

`computer.*` は OS 特権を必要とするが、それは `os.*` で Shell に依頼する（B3）。out-of-process にしてもプロセスが1つ増えるだけで、権限の観点では何も減らない。

### 第3の runtime: `stage`（Renderer 専用）

`CharacterRenderer` の実装（VRM / Live2D）は **Stage WebView 内（TypeScript）で動く**。Core 内でも別プロセスでもないため、`runtime: stage` として区別する。

| | **Renderer Extension** |
|---|---|
| `runtime` | `stage` |
| 例 | VRM / Live2D / MMD |
| 実行 | **Stage WebView 内**（TypeScript） |
| 信頼 | `official`（Phase 9 まで） |
| 権限 | Stage の範囲のみ。OS 特権も Core 状態変更も持たない |
| interface | [`CharacterRenderer`](../interfaces/renderer.md) |

**Phase 1〜8 では VRM 実装が Stage に直接組み込まれており、Extension 機構は使わない。** Phase 9 で Live2D を追加する時点で、初めて `runtime: stage` の Extension として切り出す。

Renderer が `Provider` protocol（`load` / `unload` / `resource_hint`）を実装しない理由は、Python 側のモデル資源管理の対象ではないため。GPU は使うが、それは WebGL の描画であり `ModelResourceManager` の管理対象（推論モデル）とは別。

### なぜ分けるのか

AIRI は両者を同一機構にしたため、**「Electron main プロセスに直接 `import()` される無制限の Extension」**になった。

```ts
// AIRI: packages/plugin-sdk/src/plugin-host/runtimes/node/loaders/fs.ts
const extensionModule = await import(entrypoint)   // Node フルアクセス。サンドボックスなし
```

一方で、STT / TTS を毎回プロセス跨ぎで呼ぶとレイテンシ予算（p50 1.2秒）が守れない。

**要件が正反対なので、機構を分ける。** マニフェスト形式は共通にし、`runtime` フィールドで区別する。

---

## 3. `in-core ⟹ official` 制約

> **`runtime: in-core` の Extension は `trust_level: official` でなければならない。**

`untrusted + in-core` の組み合わせは manifest 検証時に**ロードを拒否する**（fail-closed）。

### 理由

in-core Provider は Core のプロセス権限をそのまま持つ。この組み合わせが存在すると、**Extension の安全設計全体が無意味になる**（Invariant 5 を迂回できてしまう）。

### 帰結

- 第三者 Extension は常に out-of-process
- Phase 9 まで、全ての Capability Extension は `untrusted` として扱う（自分で書いたものも含む）
- **第三者製 Provider を許すかは Phase 9 で判断する**。許す場合は out-of-process Provider という第3の機構が必要になり、レイテンシとのトレードオフを再検討する

---

## 4. 信頼レベル

〔manifest 欄は今予約。実装は Phase 9〕

| レベル | 意味 | 既定の扱い |
|---|---|---|
| `official` | Lumi 公式 | 既定権限が広い。警告なし |
| `verified` | 署名済み・レビュー済み | 中間。初回のみ同意 |
| `untrusted` | 個人作 | 最小権限。毎回警告。危険 capability は都度承認 |

**フィールドを今予約する理由**: 後方互換のため。manifest スキーマに後からフィールドを足すと、既存の Extension が全部無効になる。

---

## 5. Manifest

```jsonc
{
  "manifest_version": 1,
  "id": "lumi.sensor-desktop",
  "version": "0.1.0",
  "name": "Desktop Sensor",

  "runtime": "out-of-process",     // "in-core" | "out-of-process"
  "trust_level": "untrusted",      // "official" | "verified" | "untrusted"

  "entrypoint": {
    "out_of_process": { "command": "python", "args": ["-m", "lumi_sensor_desktop"] }
  },

  // 天井（ceiling）。実効権限はこれと policy と user grant の交差
  "capabilities": {
    "tools": [
      { "name": "fs.read", "risk": "L2", "lane": "fs",
        "scope_hint": "user_home",
        "reason": "会話中に言及されたファイルを読むため" }
    ],
    "sensors": [
      { "key": "user.present",       "ttl_ms": 60000 },
      { "key": "user.focus_app",     "ttl_ms": 30000 },
      { "key": "desktop.fullscreen", "ttl_ms": 30000 }
    ],
    "signals": ["sensor.*"]
  }
}
```

### `reason` フィールド

**ユーザーへの説明用。同意 UI に表示する。** Policy はこれを見ない（Invariant 1）。

AIRI の `ModulePermissionSpec` にも `reason` / `label` があり、コメントに *"Human-facing explanation for consent/permission UI"* と書かれているが、**その UI の実装が存在しない**。Lumi は Phase 4a で必ず実装する。

---

## 6. ライフサイクル

```
discover      manifest を読む
  ↓
validate      schema 検証 / runtime × trust_level の組み合わせ検証（fail-closed）
  ↓
consent       初回のみ。ユーザーに capability を提示して同意を得る ★
  ↓
load          in-core: import / out-of-process: プロセス起動 + WS 接続 + token 認証
  ↓
announce      Extension が提供する tool / sensor を Core に登録
  ↓
ready
  ↓
（実行）
  ↓
unload        Core が停止を指示 → プロセス終了 / 登録解除
```

### ★ consent を飛ばさない

**AIRI はここが無い。** `permissionResolver` 未指定で manifest がそのまま granted になる。

Lumi では consent 無しに ready にならない。同意結果は `extensions.granted_permissions_json` に永続化し、manifest が変わったら再同意を求める。

### 障害時

| 障害 | 対応 |
|---|---|
| Extension プロセスが落ちた | Core が検知 → 該当 capability を無効化 → **Lumi は動き続ける** |
| Extension が応答しない | タイムアウト → 該当 tool 呼び出しを失敗させる |
| manifest 検証失敗 | ロードしない。ログとユーザー通知 |
| in-core Provider の load 失敗 | 該当 Provider を無効化。代替があれば切り替え |

**Extension の障害で Lumi 本体が止まってはならない。**

---

## 7. Capability Extension のプロトコル

`ext.*` namespace。WS または stdio。

| 方向 | メッセージ | 内容 |
|---|---|---|
| Core → Ext | `ext.tool.invoke` | ツール呼び出し。**正規化済み `SecurityScope` のみ。Handle は渡さない** |
| Ext → Core | `ext.tool.result` | 結果 + `acted_on`。Core が `provenance = untrusted` を付与し、`ResultVerifier` で検証 |
| Ext → Core | `ext.sensor.push` | **Signal**。Core が検証して World facet を更新 |
| Core → Ext | `ext.lifecycle.*` | ready / shutdown |

詳細 → [../interfaces/extension.md](../interfaces/extension.md)

### Extension は DomainEvent を送れない

プロトコルに DomainEvent を送る経路が**存在しない**（[../contracts/event-model.md](../contracts/event-model.md)）。Extension が送れるのは Signal と Response のみ。

これは静的検査で保証する。

---

## 8. Provider Extension

Core 内実行。詳細 → [../interfaces/provider.md](../interfaces/provider.md)

```python
class Provider(Protocol):
    id: str
    kind: ProviderKind        # llm | stt | tts | embedding | vision

    async def load(self) -> None: ...
    async def unload(self) -> None: ...
    def resource_hint(self) -> ResourceHint: ...
```

### `load` / `unload` / `resource_hint` を Phase 1 で入れる理由

Phase 1 には Vision が無いので `ModelResourceManager` は不要（Phase 5 に Deferred）。

しかし**後から Provider にライフサイクルを追加すると全 Provider の書き換えになる**。窓口だけ先に確保しておけば、Phase 5 は Manager を上に被せるだけで済む。

---

## 9. Content Pack

**コードを含まない。データのみ。**

```
content/characters/lumi/
├── character.toml        # 名前、人格プロンプト、既定の mood
├── model.vrm
├── voice.toml            # TTS provider 設定、話者ID、速度、【credit】
├── expressions.toml      # ExpressionIntent → VRM ブレンドシェイプのマッピング
├── LICENSE/              # 同梱アセットのライセンス全文
└── motions/
```

### なぜコードを含めないのか

Content Pack は共有・配布されやすい。**コードを含むと、Content Pack が Extension と同じ脅威になる。**

キャラクターの「振る舞い」を変えたい場合は、人格プロンプトと表情マッピングで表現する。それで足りないなら Extension として作る。

### クレジットとライセンスの宣言〔ADR-019〕

**`voice.toml` と `character.toml` は、使用するアセットのクレジット・ライセンス情報を持たなければならない。**

```toml
# voice.toml
[credit]
speaker_name  = "ずんだもん"          # 音源名
credit_text   = "VOICEVOX:ずんだもん"  # 規約が要求する表記そのもの
license_name  = "ACML 1.0"
license_file  = "LICENSE/acml-1.0.txt"  # 同梱時は全文が必須
license_url   = "https://..."
```

| 規則 | 理由 |
|---|---|
| **`[credit]` が無い Content Pack は読み込まない**（fail-closed） | クレジット表記は音源規約上の義務であり、欠けたまま配布すると違反になる。**「後で足す」ができない性質のもの** |
| 同梱アセットは**ライセンス全文を `LICENSE/` に含める** | ACML「配布する場合は必ずライセンス文書も一緒に添付してください」 |
| **Core は `credit_text` を解釈しない。** そのまま Stage に渡す | 規約が要求する表記は権利者が決める。Core が整形すると要求を満たさなくなりうる |
| **`[model]` を宣言するなら `[model.credit]` も宣言する**（fail-closed） | 同上。**その license がクレジットを要求するかどうかとは別の判断**（既定同梱モデルは表記不要だが Lumi は出す） |
| `[model]` が無い Content Pack は**読める**（プレースホルダで動く） | 声だけの Content Pack は正当な Content Pack。**モデルを宣言したのに実体が無い**場合だけ失敗させる |

```toml
# character.toml — [character] と並ぶトップレベルの表
[model]
file = "model.vrm"
format = "vrm0"          # vrm0 / vrm1

[model.credit]
name        = "光莉 / ひかり"
credit_text = "3Dモデル: 光莉 / ひかり（あわ）"
license_name = "VRoid Hub 利用条件（作者設定）"
license_url  = "https://..."
license_file = ""        # 同梱するなら LICENSE/ の全文パスが必須
```

**モデルの実体を WebView に届けるのは Shell**（[ADR-029](../decisions/ADR-029-content-pack-asset-delivery.md)）。
Core は「どれか」を決めてパスを配り、**ファイルを配信しない**。

エンジン側のクレジットは Content Pack ではなく `Provider.attribution()` が持つ（[../interfaces/provider.md](../interfaces/provider.md)）。**モデルは Content Pack が選び、エンジンは Provider が決めるため。**

詳細 → [../licensing.md](../licensing.md) §6

---

## 10. Hook

Extension は Hook を登録できる。**一覧と veto 可否は [../contracts/event-model.md](../contracts/event-model.md) が唯一の定義場所。**

- 固定セットのみ。**セットを増やすには ADR を要求する**（乱用を避けるため）
- 宣言できるのは manifest の `capabilities.hooks` に書いたものだけ
- **観測と拒否はできるが、任意の状態書き換えはできない**（Invariant 6）

---

## 11. AIRI との比較

| | AIRI | Lumi |
|---|---|---|
| 機構 | 1つ（Extension） | **2つ**（in-core Provider / out-of-process Capability） |
| 隔離 | **なし**。`await import()` で Electron main に直接ロード | 第三者は常に別プロセス |
| 権限モデル | 交差モデルは実装済み（良い） | 同じ交差モデルを採用 |
| 同意 | **なし**。manifest が自動 granted | **初回同意 UI 必須** |
| 権限 UI | **存在しない**（型にコメントだけある） | Phase 4a で実装 |
| tool call 承認 | **完全に非存在**。IPC ハンドラが無ゲート直呼び | Permission Kernel 必須（Invariant 2） |
| プロセス分離の型 | `PluginTransport` に websocket / worker / electron があるが、**`in-memory` 以外すべて未実装** | out-of-process が既定 |
| 実際の Extension | chess は**ソース欠落**、bilibili / homeassistant は `console.warn('WIP')` 1行 | — |

**借りるのは交差モデルの考え方だけ。** 実行機構と同意フローは独自に設計する。

---

## 12. テスト

| # | テスト |
|---|---|
| 1 | `untrusted + in-core` の manifest がロード時に拒否される |
| 2 | manifest 検証失敗で Extension がロードされない |
| 3 | consent なしに Extension が ready にならない |
| 4 | manifest が変わったら再同意を求める |
| 5 | Extension が宣言外の capability を使おうとすると拒否される |
| 6 | Extension が宣言外の sensor key を送ると拒否される |
| 7 | Extension プロセスの異常終了で Core が落ちない |
| 8 | Extension のタイムアウトで該当 tool 呼び出しが失敗し、他は動き続ける |
| 9 | **Extension プロトコルに DomainEvent を送る経路が無い**（静的検査） |
| 10 | Extension の出力が `provenance = untrusted` になる |
| 11 | `before_tool` Hook の veto がツール実行を止める |
| 12 | Hook が Core の状態を直接書き換えられない |
| 13 | Content Pack にコードが含まれていたら読み込まない |
| 13b | **Content Pack の `voice.toml` に `[credit]` が無ければ読み込まない**（fail-closed） |
| 13c | **同梱アセットの `license_file` が実在しなければ読み込まない** |
| 14 | **Class A の lane を宣言した out-of-process manifest が拒否される**（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)） |
| 15 | **`fs` / `computer` が Extension として登録されていない**（静的検査） |
