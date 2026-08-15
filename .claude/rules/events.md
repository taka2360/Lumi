---
paths:
  - "core/lumi/kernel/event*.py"
  - "core/lumi/kernel/command*.py"
  - "core/lumi/kernel/hook*.py"
  - "core/lumi/transport/**/*.py"
---

# Event Model — Signal / DomainEvent / Command

定義 → [contracts/event-model.md](../../docs/contracts/event-model.md), [ADR-010](../../docs/decisions/ADR-010-signal-vs-domain-event.md)

> **外から来るものと、Core が「起こった」と宣言するものは、同じ型であってはならない。**

| | **Command** | **Signal** | **DomainEvent** |
|---|---|---|---|
| 意味 | 「実行せよ」 | 「外部からこう通知された」 | 「Lumi の世界でこれが起きた」 |
| 発行者 | Core 内部 | Shell / Stage / Ext / Widget | **Core Kernel のみ** |
| 戻り値 | **あり。await する** | なし | なし |
| 順序 | 保証。backpressure あり | 保証しない | **per-stream で保証** |
| 永続化 | しない | **しない**（監査には残す） | **する** |

## 型で塞ぐ

- **`Signal` は `stream_key` / `sequence_id` を持たない。** これで「外部が DomainEvent を直接書く」経路がコンパイル時に塞がる
- **`DomainEventDraft` と `DomainEvent` を別の型にする。** 発行者が `sequence_id` を触れないことを型で保証する
- **Extension プロトコルに DomainEvent を送る経路を作らない**

## 採番は EventBus の独占

1. 発行者は `sequence_id` を持たない draft を publish する
2. EventBus が `stream_key` ごとに**直列化して**採番する
3. **採番と永続化は同一トランザクション**（分けるとクラッシュ時にギャップが生まれ、ギャップ検出が無意味になる）
4. **永続化してから配送する**
5. 消費側は欠番・逆転を検出したら**エラーにする**（黙って処理しない）

## 判断

```
結果が必要か？ → Yes: Command
              → No ↓
外から来たか？ → Yes: Signal
              → No : DomainEvent（Core が発行）
```

**迷ったら Command。** あとから Event に緩めるのは簡単だが、Event で組んだ制御フローを Command に直すのは難しい。

> **制御フローが DomainEvent の観測に依存してはならない。**
> AIRI は約60種の WS イベントでモジュールのライフサイクルを振り付けており、追跡不能になっている。
> **Lumi はライフサイクルを明示的な Command シーケンスにする。**

## Signal の昇格

```
Signal 受信 → 送出元の認証 → schema 検証 → capability 検査
           → Core が意味を解釈 → 状態変更 → DomainEvent 発行
```

**外部は DomainEvent の内容を直接決められない。** Signal は素材であり、どう表現するかは Core が決める。
Stage は「予算を減らせ」とは言わない。「ユーザーがうるさいと言った」と伝えるだけ。

## Hook

固定セット（一覧 → [contracts/event-model.md](../../docs/contracts/event-model.md)）。同期的・順序保証あり。

- 戻り値は **`Continue` か `Veto(reason)` のみ**。状態を返して Core が適用する設計にしない
- **観測と拒否はできるが、任意の状態書き換えはできない**（Invariant 6）
- **セットを増やすには ADR を要求する**

## Crash Recovery

副作用を持つ Command には `idempotency_key` が必須。3段記録（`INTENT_RECORDED` → `EXECUTION_STARTED` → `EXECUTION_CONFIRMED`）を Phase 1 から通す（**記録するだけ。復旧ロジックは Phase 4a**）。

`idempotency_key = hash(activity_id, tool_name, security_scope, args_digest)` — **生の引数ではなく `SecurityScope` を含める**。
