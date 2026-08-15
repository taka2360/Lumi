---
paths:
  - "core/**/*.py"
---

# Lumi Core — Python

## Core の定義

> **Core は権威を持つが、能力の実装を持たない。**

Core が持つのは Decision / State / Policy / Scheduling / Coordination / Memory。
Browser / Game / Sensor / Vision model は持たない。

例外は `core/lumi/tools/builtin/`（`fs` / `computer` など）。これは「能力を Core に置いた」のではなく、
**Kernel 実行契約が in-core でしか成立しないため**（[ADR-017](../../docs/decisions/ADR-017-out-of-process-tool-contract.md)）。

## 依存の向き — 逆流させない

```
AttentionArbiter ──→ (ReactiveLoop | DeliberativeLoop)
Reactive/Deliberative ──→ Memory, World, Internal, ProviderRegistry, ToolRegistry
ToolRegistry ──→ PermissionKernel ──→ (Canonicalizer, Policy, GrantStore, AuditLog)
すべて ──→ EventBus（発行のみ）
```

| 禁止 | 理由 |
|---|---|
| `kernel/` が他モジュールに依存する | kernel は型と調停だけを持ち、具体的な能力を知らない |
| `PermissionKernel` → `Tool` | Kernel が個別ツールを知ると、ツール追加のたびに Kernel が変わる |
| `Memory` → `Agent` | 記憶はエージェントの都合を知らない。検索クエリを受け取るだけ |
| `World` → `Sensor` | Core は Sensor の実装を知らない。Signal を受け取るだけ |
| `EventBus` → 何か | Bus は誰も知らない |

**`kernel/` が他のどのモジュールにも依存しないことを静的検査で保証する。**

## 書き方

- **型を書く。** 契約に登場する型は `@dataclass(frozen=True)` か `Protocol`
- **`SecurityScope` / `ToolResult` / `Signal` / `DomainEvent` / `AuditRecord` は必ず不変**（frozen）。可変にすると TOCTOU・監査不能・履歴改竄の穴になる
- `asyncio` 単一プロセス。**ブロッキング I/O と推論はスレッドに逃がす**（イベントループを止めない）
- ログは `structlog` の構造化ログ。`correlation_id` / `activity_id` を必ず載せる
- 外部エンジン（Ollama / AivisSpeech）が居ないときは**明示的に失敗させる。黙って劣化しない**

## テスト

**LLM を呼ばずにテストできること。呼ばないとテストできないなら設計が間違っている。**
→ `.claude/rules/tests.md`

## 迷ったら

- 「これを外しても Lumi は Lumi か？」→ Yes なら Extension、No なら Core
- 「結果が要るか？」→ Yes なら Command、No で外から来たなら Signal、No で Core 発行なら DomainEvent
- fail-open か fail-closed か迷ったら **fail-closed**
