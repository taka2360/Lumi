# Agent Architecture — Attention Arbiter と Reactive Loop

> Lumi が「今なにをしているか」を管理する仕組みと、会話の流れ。

親: [DESIGN.md](../DESIGN.md) / 関連: [../contracts/state-machines.md](../contracts/state-machines.md), [autonomy.md](autonomy.md), [audio.md](audio.md)

---

## 1. Attention Arbiter — 最重要コンポーネント

### 解決する問題

会話ループ・自律ループ・タスク・ゲームを別々に動かすと、必ずこうなる。

```
Lumi 「ねえねえ！」
ユーザー「今ちょっと――」
Lumi 「あとゲームで――」
ユーザー「おい」
```

さらに barge-in のとき「何を止めるべきか」が分散すると、止め忘れが必ず起きる。

### 解

**「今 Lumi がしていること」を単一の権威が所有する。**

```python
@dataclass
class Activity:
    id: ActivityId
    kind: ActivityKind          # conversation | autonomous | task | game | idle
    actor: Actor                # user_initiated | self_initiated | scheduled | system
    priority: int
    interruptible_by: set[int]
    state: ActivityState        # contracts/state-machines.md
    cancel_token: CancelToken
    deadline: datetime | None
    parent: ActivityId | None
    children: list[ActivityId]  # ツール呼び出しは子 Activity


class AttentionArbiter:
    _foreground: ActivityId                    # 常に有効。None にならない
    _background: dict[ActivityId, Activity]    # cancelling / suspended
    _deferred: DeferredQueue

    def propose(self, p: ActivityProposal) -> Accepted | Deferred | Rejected: ...
    def interrupt(self, reason: str) -> InterruptResult: ...
    def current(self) -> Activity: ...         # None を返さない
    def inference_lease(self, holder: Job) -> InferenceLease: ...   # §5
```

### 不変条件

1. **`_foreground` が指す Activity は常にちょうど1つ**（Invariant 4）
2. **`running` を取れるのは foreground だけ**（背景に居るのは `cancelling` / `suspended`）
3. **Activity の状態遷移を実行できるのは Arbiter のみ**
4. **Activity の中断は `interrupt()` 以外の経路で起きない**（再生バッファのミュートは Activity 状態遷移ではないので対象外 → §2）

### idle Activity

何もしていない時は `kind=idle` の Activity が `running` で存在する。起動時に生成され、`proposed`/`accepted` を経ない唯一の例外。

**他の Activity が foreground の間、idle は `suspended`。** 消滅はしない。

詳細と根拠 → [../contracts/state-machines.md](../contracts/state-machines.md), [ADR-016](../decisions/ADR-016-always-one-activity.md), [ADR-018](../decisions/ADR-018-foreground-and-jobs.md)

### propose の判定

```python
def propose(self, p: ActivityProposal) -> Accepted | Deferred | Rejected:
    cur = self.current()

    if p.priority in cur.interruptible_by:
        new = self._accept(p)                        # 3. accepted
        self._begin_interrupt(cur, reason=f"preempted_by:{p.kind}")   # 1-2
        self._foreground = new.id                    # 4. ★切り替え
        self._to_running(new)                        # 5.
        self._to_background_cancelling(cur)          # 6. 非同期に子を止める
        return Accepted(new)

    if p.deferrable:
        return self._deferred.offer(p)               # Deferred(retry_after=...)

    return Rejected(reason="busy")
```

**`_foreground` の切り替えは、旧 Activity の子 Tool の停止を待たない。** 待つと barge-in が遅れる。旧は background で `cancelling` を続ける。

遷移順序の詳細と、なぜこの順序でなければならないか → [../contracts/state-machines.md](../contracts/state-machines.md)

- 会話中の自律提案 → 通常 `Deferred`（自律は deferrable。`DeferredQueue` が TTL 付きで保持する）
- idle 中の自律提案 → 通常 `Accepted`
- 会話中のユーザー発話 → `Accepted`（ユーザー入力は最高優先度）

---

## 2. Cancellation と barge-in

**`cancel_token.fire()` で全部止まるという仮定は誤り。**

3つの契約（`cooperative` / `hard` / `non_cancellable`）と barge-in の手順は
**[../contracts/state-machines.md](../contracts/state-machines.md) が唯一の定義場所。**

Agent 側で押さえるべき点だけ再掲する。

| # | 点 |
|---|---|
| 1 | **「音が止まる」は VAD スレッドが同期的に行い、Arbiter を経由しない**（[audio.md](audio.md)）。Activity 状態遷移ではないので Invariant 4 の対象外 |
| 2 | **「Activity が止まる」は asyncio 側で `arbiter.interrupt()` が行う** |
| 3 | `non_cancellable` な子が残るなら旧 Activity は `abandoned`。その結果は Memory にも PromptContext にも入らない |
| 4 | **`InterruptResult` が「何が止まり、何が abandoned になったか」を返す。** Inspector に表示してデバッグ可能にする |

---

## 3. Reactive Loop（会話）

```
speech-end
  ↓
STT
  ↓
Activity 提案: Activity(kind=conversation, actor=user_initiated)
  ↓
MemoryRetrieval(budget=N tokens)
  ↓
PromptAssembly
  ↓
LLM stream
  ├─ text delta   → 文分割 → TTS → 再生 → リップシンク
  ├─ <|ACT ...|>  → ExpressionIntent → stage.character.expression
  └─ tool call    → Kernel実行契約 → ToolResult(untrusted) → 再投入
  ↓
EpisodeRecord 書き込み（記憶化はまだしない → memory.md）
```

### PromptAssembly の構成

```
                                                         trust への寄与
1. persona            （Content Pack から）              trusted
2. world 投影         （World State の圧縮）             trusted
3. internal state     （mood / current_goal）            trusted
4. retrieved memory   （予算内。assertion_mode 付き）     block_trust
5. recent turns       （Working Memory）                 history_trust
6. ContextBlock[]     （ツール結果等。provenance 付き）    block_trust
7. 現在の発話                                            trusted（ユーザー入力）

effective_trust = join(block_trust, history_trust, session_trust)
```

**トークン予算を固定し、超過時に何を落とすかを決定論的に決める。** LLM に「適当に切る」をさせない。

#### trust の集計

`effective_trust` は3つのスコープの join であり、**`session_trust` は sticky**（一度 tainted になったらセッション終了まで戻らない）。

これが無いと、untrusted ブロックが予算超過で文脈から落ちた瞬間に taint が消え、Invariant 7 が破れる。

**規則の定義は [../contracts/provenance.md](../contracts/provenance.md) の §会話履歴の trust。**

#### untrusted ブロックの隔離

隔離ブロックの書式は [../contracts/provenance.md](../contracts/provenance.md) が唯一の定義場所。

**これは防御の一枚に過ぎない。** 最終防衛線は Policy 側の強制昇格。

#### assertion_mode による提示の差

| assertion_mode | 提示 |
|---|---|
| `user_confirmed` / `user_stated` | 事実として提示 |
| `inferred` | **「〜と思われる（会話から推測）」** |
| `self_generated` | **「〜とわたしは思っている（根拠は自分の推測）」** |

これにより Lumi が「わたしがそう思ってるだけかもだけど」と言えるようになる。→ [memory.md](memory.md)

### ツールループ

```python
step = 0
while step < ctx.max_steps and not ctx.cancel_token.is_set():
    ...
    if tool_call:
        result = await tool_registry.invoke(tool, ctx, args)   # Kernel実行契約
        messages.append(as_context_block(result))              # provenance 付き
        step += 1
        continue
    break
```

- 上限（ステップ数・deadline・トークン予算）は **Activity が保持する**
- 各ステップで `cancel_token` をチェック（`cooperative`）
- ツール結果は `ProvenanceClass = untrusted` → `block_trust` と `session_trust` を汚染する
- **同一ターン内でも、ツール結果が入った後の tool call は `effective_trust = TAINTED` で判断される**（実効 L3 以上は `ask`）

### インラインマーカー

LLM ストリーム内の `<|ACT {"emotion":"happy","intensity":0.7}|>` を表情/モーション制御に使う（AIRI のアプローチを借用）。

- ストリーミング中にパースし、マーカーは**音声化前に除去**する
- パース失敗時はマーカーごと落とす（読み上げない）
- 未知の emotion は Renderer 側でフォールバック（[../interfaces/renderer.md](../interfaces/renderer.md)）

---

## 4. Hook

**一覧と veto 可否は [../contracts/event-model.md](../contracts/event-model.md) が唯一の定義場所。**

固定セット。同期的・順序保証あり。DomainEvent とは別の仕組み。乱用を避けるため、セットを増やすには ADR を要求する。

**Hook は観測と拒否はできるが、任意の状態書き換えはできない**（Invariant 6）。

---

## 5. Job — foreground を取らない処理

**Reflection Job・再埋め込み・DB メンテナンスは Activity ではない。**

```python
@dataclass
class Job:
    id: JobId
    kind: JobKind                # reflection | reembedding | maintenance
    actor: Actor                 # 常に system
    cancellation: Cancellation
    cancel_token: CancelToken
    uses_inference: bool
```

| | `Activity` | `Job` |
|---|---|---|
| foreground | 取る（1つだけ） | **取らない** |
| 発話 | する | **しない** |
| actor | 4種 | **`system` 固定 → L0 のみ** |
| barge-in | 対象になる | `uses_inference` なら**推論を明け渡す** |

### なぜ Activity にしないのか

Reflection は**会話と同時に進んでよい処理**である。Activity にすると foreground を占有し、「Lumi は今なにをしているか」が「記憶の整理」になってしまう。かといって同時実行を認めると Invariant 4 が意味を失う。

→ [ADR-018](../decisions/ADR-018-foreground-and-jobs.md)

### `inference_lease` — 推論資源の調停

**Job が Arbiter の管理外にあると barge-in が破れる。** Reflection Job は LLM を呼ぶため:

```
Reflection Job が LLM で記憶を抽出中（GPU 占有）
  → ユーザーが話しかける
  → Job は止まらない
  → 会話の LLM 初トークンが Reflection の完了待ちになる
  → p95 2.0 秒 の SLO を直撃する
```

そこで、**`uses_inference = True` の Job は Arbiter から lease を取る。**

```python
async with arbiter.inference_lease(job) as lease:
    async for ev in llm.stream(..., cancel_token=lease.token):
        ...
        # foreground が推論を要求すると lease.token が fire する
```

| 規則 | 内容 |
|---|---|
| 1 | foreground Activity が推論を要求したら、**Job の lease は即座に revoke される** |
| 2 | revoke された Job は `cooperative` として中断し、**後で再開する**（進捗は捨てて良い設計にする） |
| 3 | Job は Activity を `propose` できる（例: Reflection の結果を話題にする）が、**自分は foreground にならない** |
| 4 | Job の `actor` は `system` 固定。**L1 以上のツールが必要なら、それは Job ではなく Activity として propose すべき仕事** |

**`inference_lease` は Phase 5 の `ModelResourceManager` とは別の資源である。** 前者は「誰が今推論してよいか」、後者は「どのモデルが VRAM に載るか」。前者は Phase 2（Reflection の登場）から必要。

---

## 6. AIRI との比較

| | AIRI | Lumi |
|---|---|---|
| Agent Loop | **存在しない**。1発話 = 1 LLM ストリーム。フェーズはプロンプト合成の3段階のみ | Attention Arbiter + Reactive/Deliberative の2ループ |
| ツールループ | xsAI の `streamText` に委譲（`stepCountAtLeast(10)`） | Core が回す。deadline / 予算 / cancel を Activity が保持 |
| 中断 | `stopAll('new-message')` で全停止。粒度が1つ | Cancellation 契約 3種 + `abandoned` 状態 |
| 「今なにをしているか」 | 単一の所有者がいない | Attention Arbiter |
| barge-in | **未実装**（発話中は入力を抑制） | Arbiter の `interrupt` 経由 |
| プロンプト圧縮 | `compactConversationEntries()` は実装済みだが**未配線の死にコード** | 予算固定 + 決定論的な切り落とし |

---

## 7. テスト

**これらは LLM を呼ばずにテストできなければならない。**

| # | テスト |
|---|---|
| 1 | `current()` が常に Activity を返す（起動直後・全終了後・cancel 後） |
| 1b | `running` な Activity が同時に2つ存在しない |
| 1c | preempt 中も `current()` が一意に定まる |
| 1d | idle が、他が foreground の間 `suspended` になる |
| 2 | 会話中の自律提案が `Deferred` になり、`DeferredQueue` に入る |
| 2b | `DeferredQueue` の提案が TTL で破棄される |
| 3 | idle 中の自律提案が `Accepted` になる |
| 4 | 会話中のユーザー発話が既存 Activity を preempt する |
| 4b | **Job が foreground を取らない** |
| 4c | **foreground が推論を要求すると Job の `inference_lease` が revoke される** |
| 4d | **revoke された Job が `cooperative` に中断し、後で再開する** |
| 4e | **Job が L1 以上のツールを呼べない**（`actor = system`） |
| 5 | `non_cancellable` な子がいる Activity の interrupt が `abandoned` になる |
| 6 | `InterruptResult` が停止した Tool と abandoned な Tool を正しく報告する |
| 7 | ツールループが `max_steps` / `deadline` / cancel で正しく抜ける |
| 8 | PromptAssembly が予算超過時に決定論的に切り落とす（スナップショットテスト） |
| 9 | `<|ACT|>` マーカーが音声化テキストから除去される |
| 10 | `before_tool` Hook の veto がツール実行を止める |
| 11 | Activity の状態遷移が Arbiter 以外から実行できない |
