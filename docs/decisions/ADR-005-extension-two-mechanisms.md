# ADR-005: Extension を in-core Provider と out-of-process Capability の2機構に分ける

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../architecture/extension.md](../architecture/extension.md), [../interfaces/extension.md](../interfaces/extension.md) |

---

## Decision

Extension を**2つの機構**に分ける。

| | **Provider Extension** | **Capability Extension** |
|---|---|---|
| 例 | LLM / STT / TTS / Embedding / Vision | Browser / Filesystem / Computer / GameAgent / Sensor / Widget |
| 実行 | **Core 内**（Python entry point） | **別プロセス**（WS / stdio、任意言語） |
| 信頼 | **`official` 必須** | untrusted。capability-gated |

manifest 形式は共通にし、`runtime: in-core | out-of-process | stage` で区別する。

`stage` は `CharacterRenderer`（VRM / Live2D）専用の第3の runtime。Stage WebView 内（TypeScript）で動くため Core 内でも別プロセスでもない。**Phase 1〜8 では VRM 実装が Stage に直接組み込まれており、Extension 機構は使わない。** Phase 9 で Live2D を追加する時点で初めて切り出す（→ [../architecture/extension.md](../architecture/extension.md)）。

さらに制約を課す:

> **`runtime: in-core` の Extension は `trust_level: official` でなければならない。**
> `untrusted + in-core` の組み合わせは manifest 検証で**ロードを拒否する**（fail-closed）。

---

## Reason

### 低レイテンシ要件とサンドボックス要件は両立しない

| 要件 | 必要なこと |
|---|---|
| STT / TTS のレイテンシ（p50 合計 0.5秒） | **プロセス跨ぎを避ける** |
| 第三者コードの安全性 | **プロセス隔離が必須** |

**同じ機構で両方は満たせない。** だから機構を2つにする。

### AIRI が実際に失敗している

AIRI は両者を同一機構にした結果、こうなっている。

```ts
// packages/plugin-sdk/src/plugin-host/runtimes/node/loaders/fs.ts
const extensionModule = await import(entrypoint)   // Electron main に直接ロード
```

- Node フルアクセス
- サンドボックスなし
- 権限チェックは Kit API 呼び出し時のみの「協調的」なもの
- 加えて `permissionResolver` 未指定で **manifest がそのまま granted**

さらに、AIRI の `PluginTransport` 型には `websocket` / `web-worker` / `node-worker` / `electron` が定義されているが、**`in-memory` 以外すべて未実装**（`createPluginContext()` が `throw new Error('... is not implemented yet.')`）。`plugin/local.ts` と `remote.ts` は中身が `export {}` の空ファイル。

**「将来プロセス分離できる型だけ用意して、実装しない」という状態が、最も危険。** 設計上は安全に見えるのに、実際には全部が特権プロセスで動く。

### `in-core ⟹ official` が必要な理由

in-core Provider は **Core のプロセス権限をそのまま持つ**。

`untrusted + in-core` が存在できると、攻撃者は manifest に `runtime: in-core` と書くだけで Invariant 5（Capability）を完全に迂回できる。**Extension の安全設計全体が無意味になる。**

manifest 検証で組み合わせを禁止することで、この穴を型レベルで塞ぐ。

---

## Alternatives

### A. 単一機構・全部 out-of-process

**利点:** 一貫している。最も安全
**欠点:** STT / TTS がプロセス跨ぎになり、レイテンシ予算（p50 1.2秒）が守れない可能性が高い。特に音声バッファの受け渡しコストが大きい

### B. 単一機構・全部 in-core（AIRI の実質的な状態）

**利点:** 最速。実装が単純
**欠点:** 第三者 Extension が Core のプロセス権限を持つ。**第三者 Extension を将来サポートする方針（要件18）と両立しない**

### C. 単一機構 + WASM サンドボックス

**利点:** 一貫していて、かつ隔離される
**欠点:**
- Python の WASM 実行が実用的でない
- STT / LLM の推論を WASM 内で動かせない（ネイティブライブラリが必要）
- Widget の生成ゲームには使えるが、Provider には使えない

### D. 単一機構 + `isolated-vm` 相当（AIRI の Minecraft 統合が採用）

**利点:** JS コードの隔離としては有効
**欠点:** Python には同等品が無い。かつ Provider（ネイティブ推論）には適用できない

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| 機構が2つある | 実装・ドキュメント・テストが2系統 |
| **第三者製 Provider を許せない** | STT / TTS を第三者が差し替えられない（Phase 9 で再検討） |
| manifest に `runtime` フィールドが要る | 検証ロジックが増える |

### 得るもの

- レイテンシ要件を満たせる
- 第三者 Extension を安全にサポートできる
- **AIRI の最大の設計欠陥を回避できる**

---

## Consequences

### 第三者製 Provider は Phase 9 で判断する

現時点では `in-core ⟹ official` により、第三者は Provider を提供できない。

Phase 9 で「第三者製 Provider を許すか」を判断する。許す場合は **out-of-process Provider という第3の機構**が必要になり、レイテンシとのトレードオフを再検討する。

**これは既知の未解決点として記録する。** 自己レビューでも「残る緊張」として挙げている。

### Capability Extension のプロトコルが必要になる

`ext.*` namespace。WS または stdio。**Extension が送れるのは Signal と Response のみ**（DomainEvent は送れない、[ADR-010](ADR-010-signal-vs-domain-event.md)）。

### 障害の隔離が得られる

| 障害 | 対応 |
|---|---|
| Capability Extension が落ちた | Core が検知 → 該当 capability を無効化 → **Lumi は動き続ける** |
| Provider の `load()` が失敗 | 該当 Provider を無効化。代替があれば切り替え |

**Extension の障害で Lumi 本体が止まってはならない。** out-of-process であることがこれを構造的に保証する。

### 同意フローが必須になる

Extension が untrusted である以上、**初回ロード時に必ず同意 UI を出す**（Invariant 5）。

AIRI は `reason` / `label` フィールドをコメント付きで用意しているが、**その UI の実装が存在しない**。Lumi は Phase 4 で必ず実装する。

### Content Pack はコードを含まない

Content Pack（キャラクター・音声・モデル）は共有されやすい。コードを含むと Extension と同じ脅威になる。

**読み込み時にコードファイルが含まれていたら拒否する。**
