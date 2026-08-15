# Authority Matrix — 誰が何をできるか

> **Status: Confirmed**
> この表は実装レビューのチェックリストである。**表に「✓」が無いことをそのコンポーネントがしていたら、それは設計違反。**

親: [DESIGN.md](../DESIGN.md) / 関連: [invariants.md](invariants.md), [security-boundaries.md](security-boundaries.md)

---

## 権限マトリクス

| Component | World読み取り | Core状態変更 | Tool実行 | 権限判断 | Signal送出 | DomainEvent発行 | OS特権 |
|---|---|---|---|---|---|---|---|
| **Core Kernel** | ✓ | ✓ | ✓（Permission経由必須） | ✓ **唯一** | — | ✓ **唯一** | ✗（Shellに依頼） |
| **LLM** | context に投影された分のみ | ✗ | **要求のみ** | ✗ | ✗ | ✗ | ✗ |
| **Stage** | 配信された投影のみ | ✗ | ✗ | ✗ | UI由来のみ | ✗ | ✗ |
| **Shell** | OS読み取り（Sensorとして） | ✗ | ✗ | ✗（Invariant 8 の拒否を除く） | ✓ | ✗ | ✓ **唯一** |
| **Provider Ext (in-core)** | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Capability Ext** | 宣言範囲のみ | ✗ | 宣言範囲（Core経由） | ✗ | 宣言範囲 | ✗ | ✗ |
| **Widget** | ✗ | ✗ | Broker経由の宣言範囲 | ✗ | Broker経由 | ✗ | ✗ |

---

## 各列の意味

### World読み取り
World State / Internal State / Memory へのアクセス。

- **Core Kernel**: 全面的
- **LLM**: プロンプトに投影された分だけ。LLM が能動的に World を読むことはない（読みたければツール呼び出し）
- **Stage**: Core が `stage.*` で配信した投影だけ。Stage が能動的に問い合わせない
- **Capability Ext**: manifest で宣言した facet だけ。宣言外は取得できない

### Core状態変更
World facet / Internal State / Memory / Activity / Grant の変更。

**Core Kernel だけが ✓。** 他はすべて Command か Signal を経由し、変更するかどうかは Core が決める。

Sensor Extension も例外ではない: Sensor は `Signal` を送るだけで、World facet を更新するのは Core。

### Tool実行
- **Core Kernel**: `ToolRegistry` を通じて実行。ただし必ず Permission Kernel を経由する（Invariant 2）
- **LLM**: **要求のみ**。tool call は「使いたい」という提案であって実行ではない
- **Capability Ext**: 自分が提供するツールを実行するが、**呼び出しは常に Core から来る**。Extension が自分でツールを起動することはない

### 権限判断
**Core Kernel の Permission Kernel だけが ✓。** これが Invariant 1。

Shell の「✗（Invariant 8 の拒否を除く）」は、Shell が**拒否のみ**をハードコードで持つことを意味する。許可の判断は持たない。

### Signal送出 / DomainEvent発行
**この2列を分けたことが重要。** → [event-model.md](event-model.md)

| | Signal | DomainEvent |
|---|---|---|
| 誰が | 外部（Shell / Stage / Ext / Widget） | **Core Kernel のみ** |
| 意味 | 「外部からこう通知された」 | 「Lumi の世界でこれが起きた」 |
| 永続化 | しない | する |

外部が DomainEvent を直接書けると、**Core が解釈していない状態変更が履歴に残る**（Invariant 6 違反）。

### OS特権
screenshot / input injection / window create / process launch。

**Shell だけが ✓。** Core は `os.*` で依頼するだけで、実行しない。
ただし Shell は無条件に従うわけではない → [security-boundaries.md](security-boundaries.md) の B3

---

## 主要オブジェクトの責務行列

**誰が生成 / 変更 / 破棄できるか。**

| オブジェクト | 生成 | 変更 | 破棄 |
|---|---|---|---|
| `Activity` | Attention Arbiter のみ（idle は起動時に自動生成） | Attention Arbiter のみ | Arbiter（状態遷移で表現。物理削除しない） |
| `Job` | Core 内部のみ（Reflection / メンテナンス） | Core 内部のみ。`actor` は `system` 固定 | 完了または cancel |
| `Grant` | Permission Kernel（ユーザー承認を経て） | 消費（`remaining_uses` 減算）のみ | 期限切れ・明示的失効。**Tool からは不可** |
| `SecurityScope` | **Canonicalizer（Kernel所有）のみ** | **不変（immutable）** | — |
| `Handle` | `Tool.bind` | 不変。`BindVerifier` の検証を通ったものだけが有効 | Tool（execute 完了時） |
| `ToolResult` | Tool Registry のみ | **不変** | — |
| `MemoryRecord` | Memory System のみ | supersede のみ（上書きしない） | archive のみ（物理削除は明示的ユーザー操作） |
| `WorldFacet` | Sensor Extension が Signal を送り、**Core が書く** | Core（上書き） | TTL 失効 |
| `InternalState` | Core 内部のみ | Core 内部のみ | — |
| `Signal` | Shell / Stage / Extension / Widget | **不変** | Core（処理後に破棄。永続化しない） |
| `DomainEvent` | **Core Kernel のみ**（EventBus が採番） | **不変** | — |
| `AuditRecord` | Permission Kernel / Tool Registry | **不変・追記のみ** | **不可（Tool からは到達不能）** |

### 不変（immutable）が多い理由

`SecurityScope` / `ToolResult` / `Signal` / `DomainEvent` / `AuditRecord` はすべて不変。

- **`SecurityScope` が不変**でないと、Policy が検査した後に scope が書き換わりうる（TOCTOU）
- **`AuditRecord` が不変**でないと、監査が意味をなさない
- **`DomainEvent` が不変**でないと、履歴が信用できない

---

## 実装レビューのチェック項目

以下は静的検査または lint で自動化する。

| # | チェック | 対象 |
|---|---|---|
| 1 | `Tool` の実装が `PermissionKernel` を import していない | Core |
| 2 | `ToolRegistry.execute` 以外から `Tool.execute` が呼ばれていない | Core |
| 3 | `Tool` の実装が `Canonicalizer` / `BindVerifier` を実装していない | Core |
| 4 | `stage/` から `os.*` の型を参照していない | Stage |
| 5 | `shell/` が AI 判断に関わる型（`Activity` / `Drive` / `MemoryRecord`）を import していない | Shell |
| 6 | Extension プロトコルに `DomainEvent` を送る経路が無い | protocol |
| 7 | `trust_level = trusted` の書き込みがユーザー確認ハンドラ以外に存在しない | Core |
| 8 | `sequence_id` の代入が `EventBus` 以外に存在しない | Core |
| 9 | `audit_log` への `DELETE` / `UPDATE` がコードベースに存在しない | Core |
| 10 | `WorldFacet` の書き込みが Signal ハンドラ以外に存在しない | Core |
| 11 | **Activity の状態遷移が `AttentionArbiter` 以外に存在しない** | Core |
| 12 | **`_foreground` への代入が `AttentionArbiter` 以外に存在しない** | Core |
| 13 | **`ext.tool.invoke` のペイロード型に Handle（fd / HWND / PID）が含まれない** | protocol |
| 14 | **Class A の lane を out-of-process Extension が提供していない** | Core |
| 15 | **Policy 判断が `decide()` 以外の場所で行われていない**（`Decision` を返す関数が1つだけ） | Core |
| 16 | **オーディオコールバック内で推論・メモリ確保・ロック取得をしていない** | Core |

---

## この表の使い方

新しいコンポーネントや機能を追加するとき:

1. **まずこの表に行を足す。** 足せないなら、それは既存のどれかに属するべき
2. ✓ を付けたい列があるなら、**なぜ必要かを説明する**。多くの場合、Command / Signal 経由で済む
3. `権限判断` 列に ✓ を付けたくなったら、**設計が間違っている**（Invariant 1）
4. `DomainEvent発行` 列に ✓ を付けたくなったら、**Signal で足りないか確認する**（Invariant 6）
