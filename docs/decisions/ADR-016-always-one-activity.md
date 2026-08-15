# ADR-016: foreground Activity を常に1つとし、idle Activity を必置にする

| | |
|---|---|
| Status | Accepted（**一部を [ADR-018](ADR-018-foreground-and-jobs.md) が修正**） |
| Date | 2026-08-14 |
| 関連 | [../contracts/state-machines.md](../contracts/state-machines.md), [../architecture/agent.md](../architecture/agent.md) |

> **[ADR-018](ADR-018-foreground-and-jobs.md) による修正（2026-08-15）**
> 1. **idle の状態は「常に `running`」ではない。** 他の Activity が foreground の間は `suspended`。常に `running` だと `running` が2つ存在し、Invariant 4 が字面で破れるため
> 2. **`foreground` を `_foreground` という単一の参照として定義した。** 本 ADR は「ちょうど1つ」を主張しながら、何が「1つ」なのかを定義していなかった
> 3. **Job の位置づけを確定した。** 本 ADR は Reflection Job を「idle 中に走るもの（`activity_id` が要る）」と書きながら、末尾で「Activity ではなく Job」とも書いており、内部で矛盾していた
>
> **決定の骨子（`current()` が `None` を返さない / idle を必置にする）は変わらない。**

---

## Decision

```python
def current(self) -> Activity:    # None を返さない
    ...
```

**foreground Activity は常にちょうど1つ。0 にはならない。**

起動時に **idle Activity** が生成される。これは `proposed` / `accepted` を経ない**唯一の例外**。他の Activity が終了すると必ず idle に戻る。

| idle Activity の属性 | 値 |
|---|---|
| `kind` | `idle` |
| `actor` | `system` |
| `priority` | 最低 |
| `interruptible_by` | すべて |
| cancel | no-op（cancel しても idle に戻るだけ） |
| 状態 | ~~常に `running`~~ → **foreground の間 `running`、それ以外は `suspended`**（ADR-018） |

---

## Reason

### `Activity | None` は矛盾していた

Invariant 4 は「同時に実行できる foreground Activity は**常にちょうど1つ**」と書いているのに、`current()` の返り値が `Activity | None` だった。

**「ちょうど1つ」と「0または1」は違う。** どちらかに決めなければ、実装時に必ず解釈が割れる。

### `None` を許すと「何もしていない時の Policy 判断」が未定義になる

idle 中にも以下が発生する。

| 発生すること | 必要なもの |
|---|---|
| Sensor が観測を push する | `activity_id`（監査ログ用） |
| Drive の tick が回る | `actor`（Policy 判断用） |
| ユーザーが設定を変更する | `correlation_id` |

> **Reflection Job はこの表に含めない**（当初は含めていた）。Job は Activity ではなく、`job_id` と `actor = system` を自分で持つ。→ [ADR-018](ADR-018-foreground-and-jobs.md)

`None` だと、これらすべてに「Activity が無い場合」の分岐が必要になる。

**特に Policy 判断が問題。** `actor` が決まらないと、Permission Kernel が判断できない。「Activity が無いときは L0 のみ」という特別ルールを作ることになり、Policy が複雑になる。

idle Activity があれば `actor = system` として自然に扱える。

### `interrupt()` の実装から分岐が消える

```python
# ✗ None を許す場合
def interrupt(self, reason):
    cur = self.current()
    if cur is None:
        return InterruptResult.empty()    # この分岐が要る
    ...

# ✓ 常に1つの場合
def interrupt(self, reason):
    cur = self.current()                  # 必ず存在する
    ...
```

**barge-in は Lumi の中核機能であり、その実装から分岐を減らせることの価値は大きい。**

### 監査ログの `activity_id` が常に埋まる

`NULL` を許す列は、必ず「NULL のときどう扱うか」の問題を生む。集計もフィルタも複雑になる。

### Drive の tick を一貫して説明できる

> **Drive の tick は「idle Activity の中で起きている」**

これにより、tick 中のツール呼び出し（World の読み取り等）も通常の Activity 内の呼び出しとして扱える。特別扱いが不要になる。

---

## Alternatives

### A. `Activity | None`（当初案）

**利点:** 「本当に何もしていない」を素直に表現できる
**欠点:**
- 全呼び出し側に null チェックが要る
- 「何もしていない時の Policy 判断」が未定義
- `interrupt()` に分岐が要る
- `activity_id` が NULL になりうる

### B. Null Object パターン（`NullActivity` シングルトン）

**利点:** null チェックが不要。実装が軽い
**欠点:** 状態を持てない。**idle 中の出来事（Sensor 観測、Drive tick）を記録できない**。監査で「いつから idle か」が分からない

### C. Activity スタックを持ち、底に idle を置く

**利点:** 「会話中に自律が割り込んで、終わったら会話に戻る」が自然に表現できる
**欠点:**
- **Invariant 4（ちょうど1つ）と矛盾する**
- 復帰の意味論が難しい（中断された会話に戻るべきか?）
- barge-in で何を止めるかが複雑になる

**Deferred な提案は「再提案」で表現する**方が単純。スタックは採らない。

### D. 複数の foreground Activity を許す

**利点:** 並行作業ができる
**欠点:** **Invariant 4 の放棄。** 「Lumi が同時に2つのことを喋る」が起きる。UX 破綻の原因そのもの

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| idle Activity が特別扱い | `proposed` / `accepted` を経ない唯一の例外 |
| cancel が no-op | 「cancel したのに終わらない」という直感に反する挙動 |
| DB に idle Activity のレコードが増える | 起動ごとに1件 |

### `proposed` を経ない例外について

状態機械に例外を作ることは望ましくない。しかし:

- idle Activity を誰かが propose する主体がいない（起動時には Arbiter しかいない）
- propose させると「idle が rejected されたらどうするか」という無意味な分岐が生まれる

**例外を1つ作る方が、無意味な分岐を作るより単純。** 状態機械のドキュメントに明記する。

### cancel が no-op であること

「cancel したのに終わらない」は直感に反するが、**idle を終わらせたら何になるのか?** という問いに答えが無い。

実装上は「cancel → idle に戻る → 既に idle なので変化なし」であり、一貫している。

---

## Consequences

### `current()` のシグネチャが確定する

```python
def current(self) -> Activity:    # Optional ではない
```

**型で「常に存在する」ことが保証される。**

### idle 中の Policy 判断が定義される

```python
Activity(kind=IDLE, actor=Actor.SYSTEM, priority=MIN)
```

`actor = system` は L0 のみ許可される。Sensor の観測も Drive の tick も L0 なので問題ない。

### 自律提案の判定が単純になる

```python
def propose(self, p):
    cur = self.current()
    if p.priority in cur.interruptible_by:    # idle は interruptible_by = すべて
        return Accepted(...)
    ...
```

**idle は必ず interrupt できる**ので、「idle 中の自律提案は通常 Accepted」が自然に導かれる。特別扱いが不要。

### テスト項目が明確になる

| # | テスト |
|---|---|
| 1 | `current()` が起動直後に Activity を返す |
| 2 | `current()` が全 Activity 終了後に Activity を返す |
| 3 | `current()` が cancel 後に Activity を返す |
| 4 | idle Activity が `proposed` を経ずに `running` で生成される |
| 5 | idle への cancel が no-op である |
| 6 | idle 中の Sensor 観測が `activity_id` を持つ |
| 7 | idle 中の Drive tick が `actor = system` で動く |

### 将来「並行 Activity」が必要になったら

例えば「会話しながらバックグラウンドでゲームをプレイする」。

**その場合は Invariant 4 の変更になるため、新しい ADR を書く。**

現時点では、バックグラウンド処理は Activity ではなく **Job**（Reflection Job のような）として扱い、foreground Activity とは別の概念にする。これにより Invariant 4 を保ったまま非同期処理ができる。

**Job の定義（`actor = system` 固定、`inference_lease` による推論資源の調停）は [ADR-018](ADR-018-foreground-and-jobs.md) で確定した。**
