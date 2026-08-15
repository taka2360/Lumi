# Crash Recovery と冪等性

> **語彙と型は Phase 1 で確定。実装は Phase 4a。**
> 後から挿入すると Kernel の型と Event 語彙を全部書き換えることになるため、語彙だけ先に固定する。

親: [DESIGN.md](../DESIGN.md) / 関連: [../contracts/state-machines.md](../contracts/state-machines.md), [../contracts/event-model.md](../contracts/event-model.md)

---

## 1. 解決したい問題

**Activity の途中で Core がクラッシュしたらどうなるか。**

```
Lumi → filesystem.write 開始 → Core crash → restart
                                    ↓
                        「書き込みは終わったのか?」
```

再起動後、Lumi は「あのファイル、書けたんだっけ?」が分からない。**分からないまま、黙って何もしない**のが最悪の挙動である。

もう1つの発生源: `Activity = abandoned` のとき、`non_cancellable` な Tool の完了を待たずに切り離す（[../contracts/state-machines.md](../contracts/state-machines.md)）。この Tool の結果も「不明」になる。

---

## 2. イベント語彙〔Phase 1 で確定〕

```python
class ToolLifecycleEvent(Enum):
    INTENT_RECORDED = "ToolIntentRecorded"      # 実行の意図を記録した（まだ実行していない）
    EXECUTION_STARTED = "ToolExecutionStarted"  # 実行を開始した
    EXECUTION_CONFIRMED = "ToolExecutionConfirmed"  # 完了を確認した
    EXECUTION_ABORTED = "ToolExecutionAborted"  # 明示的に中断した（結果は無い）
    EXECUTION_UNKNOWN = "ToolExecutionUnknown"  # intent あり confirm なし。結果が不明
```

`stream_key = activity:<activity_id>` に属するため、順序が保証される。

### 3段記録

```
INTENT_RECORDED    ← Policy を通過し、bind/verify も済んだ。これから execute する
       ↓
EXECUTION_STARTED  ← execute に入った
       ↓
EXECUTION_CONFIRMED ← 結果を受け取り、永続化した
```

**`INTENT_RECORDED` があって `CONFIRMED` / `ABORTED` が無いものが「未確定」。**

### なぜ `INTENT_RECORDED` と `EXECUTION_STARTED` を分けるのか

- `INTENT_RECORDED` だけあって `STARTED` が無い → **実行されていない可能性が高い**（再実行してよい）
- `STARTED` があって `CONFIRMED` が無い → **実行された可能性がある**（再実行してはいけない）

この区別が無いと、全部を「実行されたかもしれない」として扱うことになり、復旧が保守的すぎて使えなくなる。

---

## 3. `idempotency_key`〔Phase 1 で型を確定〕

副作用を持つ Command には必須。

```python
@dataclass(frozen=True)
class Command:
    id: CommandId
    type: str
    payload: dict
    correlation_id: str
    idempotency_key: str | None    # 副作用を持つなら必須
```

### 必須になる Command の種類

`write` / `send` / `delete` / `launch` / `input` — つまり `side_effect != none` のツールを呼ぶ Command。

### key の生成

```python
idempotency_key = hash(activity_id, tool_name, security_scope, args_digest)
```

**`SecurityScope` を含める。** 生の引数ではなく正規化後の対象を使うことで、「同じ操作」の判定が正しくなる。

---

## 4. 復旧の方針〔Phase 4a で実装〕

```
起動時
  ↓
domain_events を走査し、INTENT_RECORDED があって
CONFIRMED / ABORTED が無いものを抽出
  ↓
各エントリについて:
```

| 状態 | Tool の性質 | 対応 |
|---|---|---|
| `INTENT_RECORDED` のみ | 任意 | **実行されていない**とみなし、必要なら再実行を提案 |
| `+ EXECUTION_STARTED` | `idempotent = True` | 再実行可 |
| `+ EXECUTION_STARTED` | `idempotent = False` | **再実行しない。** Lumi がユーザーに報告する |

### 「分からない」と言う

```
Lumi「ごめん、さっき落ちちゃった。
     README.md に書き込もうとしてたんだけど、
     終わったかどうか分からないんだ。確認してもらえる?」
```

**黙って再実行しない。黙って放置もしない。** 不確実性をユーザーに伝えるのが正しい挙動。

### 中断された Activity

```python
activity.outcome = Outcome.INTERRUPTED_BY_CRASH
```

**そのこと自体を Episode として記憶する。** これにより Lumi が「さっき落ちちゃった」と言える。

これは体験上重要で、クラッシュを「なかったこと」にすると Lumi が記憶喪失のように見える。

---

## 5. `abandoned` からの `unknown`

`Activity = abandoned` かつ `Tool = executing` の場合（barge-in で `non_cancellable` を切り離した）。

```
Tool が完了した
  → 結果は abandoned_result として監査ログにのみ記録
  → Memory にも PromptContext にも入らない
  → EXECUTION_CONFIRMED は記録するが、Activity は既に abandoned

Tool の完了を追跡しないことにした場合
  → EXECUTION_UNKNOWN
```

**どちらの場合も、結果は Lumi の文脈に入らない。** ユーザーは既に別の話をしているため。

ただし、副作用があった場合はユーザーに伝える必要がありうる。Phase 4a で「abandoned な副作用の通知」を検討する。

---

## 6. Phase ごとの実装範囲

| Phase | 内容 |
|---|---|
| **1** | **イベント語彙の定義**。`idempotency_key` の型。`Command` / `Tool` に `idempotent` フラグ。3段記録の実装（記録するだけ、復旧はしない） |
| **4** | 起動時の未確定検出。再実行判定。ユーザーへの報告。`INTERRUPTED_BY_CRASH` の記憶化 |

### Phase 1 で語彙だけ固定する理由

Event 語彙と `idempotency_key` は**全ての Command / Tool のシグネチャに影響する**。

Phase 4a で足そうとすると:
- 全 Tool に `idempotent` を追加
- 全 Command に `idempotency_key` を追加
- 全ての実行経路に3段記録を挿入

これは事実上の書き直しになる。**Phase 1 では記録するだけで復旧ロジックは書かない**が、型と経路だけ通しておく。

---

## 7. 何を復旧しないか（明示）

| 対象 | 理由 |
|---|---|
| LLM ストリームの途中 | 再生成すればよい。副作用がない |
| TTS 再生の途中 | 同上 |
| 進行中の会話の文脈 | Working Memory はセッション単位。復旧しない。「さっきの話、なんだっけ」でよい |
| Drive の瞬間値 | Internal State は永続化するが、tick ごとに再計算されるので厳密な復旧は不要 |
| World facet | TTL で失効する。再起動後は Sensor が再度 push する |

**復旧するのは「副作用の不確実性」だけ。** 状態は再構築できるものが多い。

---

## 8. テスト

| # | テスト |
|---|---|
| 1 | 3段記録が正しい順序で `domain_events` に入る |
| 2 | `INTENT_RECORDED` のみのエントリが「未実行」と判定される |
| 3 | `+ STARTED` のエントリが `idempotent` に応じて分岐する |
| 4 | `idempotent = False` の未確定が再実行されない |
| 5 | 未確定がユーザーに報告される |
| 6 | `INTERRUPTED_BY_CRASH` が Episode として記憶される |
| 7 | `idempotency_key` が `SecurityScope` を含めて生成される |
| 8 | 副作用を持つ Command が `idempotency_key` なしで作れない（型または実行時） |
| 9 | `abandoned` な Tool の結果が Memory / PromptContext に入らない |
| 10 | `abandoned_result` が監査ログに記録される |
| 11 | **Core を強制終了して再起動するシナリオテスト**（Phase 4a） |
