# ADR-024: Activity の priority を表から決め、割り込み可否を単一の閾値で判定する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-16 |
| 関連 | [../architecture/agent.md](../architecture/agent.md) §1, [../contracts/state-machines.md](../contracts/state-machines.md), [ADR-016](ADR-016-always-one-activity.md), [ADR-018](ADR-018-foreground-and-jobs.md), [../architecture/permission.md](../architecture/permission.md), [../roadmap.md](../roadmap.md) 未確定事項 #10 |

---

## Decision

roadmap 未確定事項 #10（`Activity.priority` の数値体系と `interruptible_by` を集合にする必要性）を、Attention Arbiter の実装前に次のように決める。

### 1. `interruptible_by: set[int]` を `interruptible_at: int` に置き換える

```python
@dataclass
class Activity:
    priority: int          # priority_of(kind, actor) が決める。任意の値を持たない
    interruptible_at: int  # この値以上の priority を持つ提案に割り込まれる
```

判定は次の1行になる。

```python
def can_preempt(proposal: ActivityProposal, current: Activity) -> bool:
    return proposal.priority >= current.interruptible_at
```

**`>` ではなく `>=`。** barge-in は「会話が会話を割り込む」＝**同一 priority による preempt** であり、`>` では成立しない。

### 2. priority は表から決まる。Activity が自由に持たない

```python
def priority_of(kind: ActivityKind, actor: Actor) -> int: ...
```

〔Provisional。値は使ってみて調整する。**10 刻みにして挿入の余地を残す**〕

| kind | actor | priority | interruptible_at | 意味 |
|---|---|---|---|---|
| `idle` | `system` | **0** | **0** | すべてに割り込まれる。消滅はしない（`suspended` になる） |
| `autonomous` | `self_initiated` | 30 | 100 | ユーザー発話にだけ割り込まれる。他の自律には割り込まれない |
| `task` | `scheduled` | 50 | 100 | 同上 |
| `game` | （Phase 8） | 60 | 100 | 同上 |
| **`conversation`** | `user_initiated` | **100** | **100** | **新しいユーザー発話に割り込まれる（barge-in）** |

### 3. priority を外部が提案できない

**LLM・Stage・Extension は priority を渡せない。** `ActivityProposal` は `kind` と `actor` を持ち、priority は Arbiter が `priority_of()` で決める。

### 4. `Job` は priority を持たない

Job は foreground を取らないため、この体系の外にある（[ADR-018](ADR-018-foreground-and-jobs.md)）。Job が推論資源で競合したときの調停は `inference_lease` であって priority ではない。

---

## Reason

### 集合方式の何が問題か

`interruptible_by: set[int]` は「割り込んでよい priority の**列挙**」である。3つの壊れ方がある。

| # | 壊れ方 | 具体例 |
|---|---|---|
| 1 | **完全一致でしか割り込めない** | `interruptible_by = {100}` の Activity に、priority 110 の提案が割り込め**ない**。より強いものが弾かれる。これは書いた本人にも気づきにくい |
| 2 | **新しい kind を足すと、既存の全 Activity を触る** | priority 70 の kind を足したら、それに割り込まれるべき全ての Activity の集合に 70 を書き足して回る。書き漏れは「なぜか割り込めない」として現れる |
| 3 | **順序が表現できない** | 集合は順序を持たないので、「これより強いものすべて」を書くには全部を列挙するしかない。列挙は必ず古くなる |

閾値は**単調**である。1つの値が「これ以上なら通す」を意味し、新しい priority が増えても既存の宣言は正しいままでいる。

### なぜ `>=` なのか — barge-in が同 priority だから

**Lumi が喋っている最中にユーザーが話しかける**、これが Phase 1 の中核である。このとき:

```
現在: Activity(kind=conversation, actor=user_initiated, priority=100)
提案: Activity(kind=conversation, actor=user_initiated, priority=100)
```

`>` にすると割り込めない。**barge-in が動かない。** これを回避するために「新しい発話だけ priority 101」のような特別扱いを入れると、priority が「重要度」ではなく「新しさ」を意味し始め、体系が崩れる。

`>=` なら「**同じ強さのものは、新しい方が勝つ**」という規則1つで済む。会話は常に最新のユーザー発話が正しい。

### なぜ priority を Activity が自由に持たないのか

**Invariant 1（判断は Core だけ）の適用である。**

priority を提案側が渡せると、LLM が「この自律行動は緊急です（priority=999）」と主張する経路ができる。Policy が LLM の理由文を見ない（[permission.md](../architecture/permission.md) の `decide()` は引数4つ）のと同じ理由で、**Arbiter も提案者の自己申告を見てはならない。**

`kind` と `actor` は Core が決める事実である。priority はそこから決定論的に導く。

### なぜ「単純に priority を比較する」ではないのか

`p.priority >= cur.priority` にすると、**「強いが割り込ませたくない」が表現できない。**

例: Phase 8 の game Activity は priority 60 だが、priority 50 の task に中断されると操作中のゲームが壊れる。逆に「優先度は低いが、いつでも中断してよい」もある（idle）。

**「どれくらい重要か」と「どれくらい邪魔されたくないか」は別の軸である。** 2つの値を持つのはそのため。

### 反例 — これをしないと何が壊れるか

| やってしまうこと | 壊れるもの |
|---|---|
| `interruptible_by` を集合のままにする | 新しい ActivityKind を足すたびに既存の宣言を書き換える。書き漏れが「割り込めない」として静かに現れる |
| `>` で判定する | **barge-in が動かない。** Phase 1 の完了条件を満たせない |
| priority を提案側に持たせる | LLM が優先度を主張できる。Invariant 1 が Arbiter 側で破れる |
| priority を1刻みで割り当てる | 後から間に挿入できず、体系ごと振り直しになる |
| Job に priority を持たせる | Job が foreground 争いに参加しているように見える。ADR-018 の分離が曖昧になる |

---

## Alternatives

### A. `interruptible_by: set[int]` のまま

**利点**: 最も表現力が高い。「priority 30 と 100 には割り込まれるが 50 には割り込まれない」のような非単調な関係が書ける。

**採らない理由**: その表現力が必要になる場面が現時点で1つも無い。**必要のない表現力は、間違える余地としてだけ働く**（設計原則7）。必要になったら、そのとき ADR を書いて集合に戻せばよい。`can_preempt()` が1関数に閉じているので、戻すコストは小さい。

### B. `p.priority > cur.priority` の単純比較（値を1つだけ持つ）

**利点**: 最も単純。持つ値が1つで済む。

**採らない理由**: barge-in（同 priority の preempt）が表現できない。「重要度」と「中断されにくさ」を1つの数に潰すことになり、game / idle の要求が両立しない。

### C. actor だけで判定する（priority を持たない）

**利点**: 「ユーザーが最優先」という実際の要件をそのまま書ける。値の体系を決めなくてよい。

**採らない理由**: `user_initiated` 同士（会話 vs ユーザーが開始したタスク）の関係が決まらない。Phase 8 の game のように、actor が同じで kind が違うものが増えると破綻する。

---

## Trade-offs

### 受け入れるコスト

- 非単調な割り込み関係を表現できない（A の利点を捨てる）
- priority の値は **Provisional** であり、使ってみて振り直す可能性がある。振り直すときは `priority_of()` の表1箇所を直す
- Activity が値を2つ持つ（`priority` と `interruptible_at`）。1つで済むと誤解されやすいので、docstring で軸の違いを明示する

### 得るもの

- **新しい ActivityKind を足しても、既存の Activity の宣言を触らない**
- 割り込み判定が**1行の純粋関数**になり、テストが表駆動で書ける
- **barge-in が特別扱いなしに成立する**（同 priority の `>=`）
- priority の決定が Core に閉じ、提案者の自己申告が入らない

### 保証しないこと

- **priority の値が正しいことを保証しない。** これは Provisional であり、Phase 3（自律）で「鬱陶しくない」を評価するときに必ず見直しの対象になる
- **割り込めたことが、実際に止まったことを意味しない。** `non_cancellable` な子 Tool が残れば旧 Activity は `abandoned` になる（[state-machines.md](../contracts/state-machines.md)）。priority は「切り替えてよいか」だけを決める

---

## Consequences

### ドキュメント

- [architecture/agent.md](../architecture/agent.md) §1 の `Activity` 定義を `interruptible_by: set[int]` → `interruptible_at: int` に更新し、`priority_of()` の表を追加する
- `propose()` の疑似コードを `can_preempt()` を使う形に更新する
- **roadmap.md 未確定事項 #10 が解消する**
- priority の表の**唯一の定義場所は agent.md** とする（DESIGN.md §12 の SSoT 表に行を足す）

### 実装

- `core/lumi/kernel/activity.py` に `priority_of()` と `can_preempt()` を純粋関数として置く
- **表駆動テスト**: 全 kind × actor の組み合わせで、期待する割り込み可否が成立すること
- 特に検証する項目:
  - 会話中のユーザー発話が既存の会話を preempt する（`>=` の確認）
  - 会話中の自律提案が `Deferred` になる
  - idle 中の自律提案が `Accepted` になる
  - `ActivityProposal` に priority を渡す経路が存在しない（静的検査）
