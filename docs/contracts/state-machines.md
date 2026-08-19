# 状態機械 — Activity と Tool

> **Status: Confirmed**
> **最重要: Activity の状態と Tool の状態は同じものではない。** `non_cancellable` なツールがあるため、両者は正当に乖離しうる。

親: [DESIGN.md](../DESIGN.md) / 関連: [invariants.md](invariants.md), [tool-execution.md](tool-execution.md), [../architecture/agent.md](../architecture/agent.md)

---

## なぜ2つに分けるのか

`cancel_token.fire()` で全部止まるという仮定は**誤り**である。

subprocess・HTTP リクエスト・Playwright 操作・GPU 推論・OS 入力インジェクションは、キャンセル方式も、そもそも中断可能かも異なる。

```
ユーザーが barge-in
  → Activity は「終わった」ことにしたい（新しい発話に応えたいから）
  → しかし実行中の GPU 推論は止められない
```

**Activity の状態と Tool の状態を同じものとして扱うと、この状況が表現できない。**

---

## foreground の定義

> **`foreground` は Arbiter が保持する単一の参照である。Invariant 4 の「ちょうど1つ」はこの参照についての言明である。**

```python
class AttentionArbiter:
    _foreground: ActivityId                    # 常に有効。None にならない
    _background: dict[ActivityId, Activity]    # cancelling / suspended なもの

    def current(self) -> Activity:
        return self._activities[self._foreground]
```

| 言明 | 真偽 |
|---|---|
| `_foreground` が指す Activity は常にちょうど1つ | **常に真**（Invariant 4） |
| `running` 状態の Activity は常にちょうど1つ | **常に真**（`running` は foreground だけが取る） |
| `Activity` オブジェクトが同時に複数存在する | **ありうる。違反ではない**（cancelling / suspended） |

根拠と選択肢 → [ADR-018](../decisions/ADR-018-foreground-and-jobs.md)

---

## Activity 状態機械

```
                    ┌──────────► rejected
                    │
 proposed ──────────┼──────────► deferred ──► (DeferredQueue → 再提案)
                    │
                    └──► accepted ──► running ──► completing ──► completed
                                       │  ▲                   └──► failed
                            (idle のみ) │  │
                                    suspended
                                         │
                                  interrupt_requested
                                         │
                                         ▼
                                    cancelling ──► cancelled
                                         │
                                         └──► abandoned
```

### 状態の意味

| 状態 | foreground か | 意味 |
|---|---|---|
| `proposed` | — | Arbiter に提案された。まだ受理されていない |
| `rejected` | — | 受理されなかった（優先度不足・禁止条件） |
| `deferred` | — | 今は受理できない。`DeferredQueue` が保持し、後で再提案されうる |
| `accepted` | — | 受理された。実行開始前 |
| **`running`** | **✓ 常に** | 実行中。**これが foreground Activity。同時に1つだけ** |
| **`suspended`** | ✗ | **idle 専用。**他の Activity が foreground の間、idle が取る状態 |
| `completing` | ✗ | 正常終了処理中（後片付け、記録） |
| `completed` | ✗ | 正常終了 |
| `failed` | ✗ | 異常終了 |
| `interrupt_requested` | ✗ | 中断が要求された（barge-in 等）。まだ子の処理は始まっていない |
| `cancelling` | ✗ | 中断処理中。子 Tool の停止を待っている。**background に居る** |
| `cancelled` | ✗ | 中断完了。**全ての子 Tool が停止または完了した** |
| `abandoned` | ✗ | 中断したが、**まだ動いている子 Tool の完了を待たずに切り離した**（下記） |

### preempt の遷移順序

**「旧が `cancelling` の間に新が `running` になる窓」は必ず開く。** barge-in を速くする以上これは正しい。順序を固定して、その窓の間も `current()` が一意に定まるようにする。

```
1. 旧 foreground → interrupt_requested
2. 即時効果（TTS 再生ミュート等）        ← Activity 状態遷移ではない
3. 新 Activity → accepted
4. _foreground を新に切り替える          ★ ここで Invariant 4 が満たされ続ける
5. 新 → running / 旧 → cancelling（background へ）
6. 旧は background で子の停止を待つ
     猶予時間内に全子が停止 → cancelled
     まだ動いている子が残る   → abandoned
```

**4 より前に旧を `cancelling` にしない。** そうすると一瞬 `running` が0個になり、Invariant 4 が破れる。

### `deferred` の所有者

`DeferredQueue` を Arbiter が所有する。詳細 → [ADR-018](../decisions/ADR-018-foreground-and-jobs.md)

| 項目 | 規則 |
|---|---|
| 保持するもの | `ActivityProposal`（Activity そのものではない） |
| TTL | 既定 10 分〔Provisional〕。超過で破棄 |
| 再提案 | foreground が idle に戻ったとき、および `retry_after` 経過時 |
| 重複 | 同一 `kind` × `intent` は1件のみ（新しい方で置換） |

**古い提案が生き残って不意に発火することを防ぐ。** 30分前に話しかけたかったことを今実行されるのは不気味である。

### `cancelled` と `abandoned` の違い

| | `cancelled` | `abandoned` |
|---|---|---|
| 子 Tool | 全て停止または完了済み | まだ動いているものがある |
| 結果の扱い | 破棄 | **完了しても破棄**（`abandoned_result` として監査ログにのみ記録） |
| いつ起きるか | 全ての子が猶予時間内に停止した | `non_cancellable` な子がいる / **`cooperative` な子が猶予時間内に停止しなかった** |

> **猶予時間内に止まらなかった `cooperative` も `abandoned` になる。**〔Phase 1 実装時に明確化〕
> `abandoned` の定義は「**まだ動いている子がある**」であって「`non_cancellable` な子がある」ではない。
> 契約違反の兆候なので警告は残すが、**待ち続けて barge-in を壊す方が悪い。**

### idle Activity — 特別扱い

**foreground Activity は常にちょうど1つ。0 にはならない。**

```python
def current() -> Activity:   # None を返さない
    ...
```

起動時に **idle Activity** が `running` 状態で生成される。これは `proposed` / `accepted` を経ない**唯一の例外**。他の Activity が終了すると必ず idle に戻る。

| idle Activity の属性 | 値 |
|---|---|
| `kind` | `idle` |
| `actor` | `system` |
| `priority` | 最低 |
| `interruptible_by` | すべて |
| cancel | no-op（cancel しても idle に戻るだけ） |
| 状態 | **他に foreground が居ない間は `running`、居る間は `suspended`** |

> **idle は「常に `running`」ではない。**（[ADR-016](../decisions/ADR-016-always-one-activity.md) の当該記述を [ADR-018](../decisions/ADR-018-foreground-and-jobs.md) が修正した）
> 常に `running` だと、会話 Activity と合わせて `running` が2つになり、Invariant 4 が字面で破れる。
> **idle は消滅しないが、foreground でない間は `suspended`。**

**`None` を許さない理由** → [ADR-016](../decisions/ADR-016-always-one-activity.md)

- 全呼び出し側の null チェックが不要
- **「何もしていない時の Policy 判断」が未定義にならない**（idle 中の Sensor 観測もツール要求も actor と activity_id を持つ）
- Drive の tick は「idle Activity の中で起きている」と一貫して説明できる
- `interrupt()` の対象が常に存在するので barge-in の実装から分岐が消える
- 監査ログの `activity_id` が常に埋まる

### 遷移を実行できるのは誰か

**Attention Arbiter のみ。** 他のコンポーネントは `propose` / `interrupt` を呼ぶだけで、状態を直接書き換えない。

---

## Job は Activity ではない

**foreground を取らない非同期処理**（Reflection Job、再埋め込み、DB メンテナンス）は `Job` として扱い、この状態機械の対象外とする。

| | `Activity` | `Job` |
|---|---|---|
| foreground | 取る（1つだけ） | **取らない** |
| 発話 | する | **しない** |
| actor | 4種 | **`system` 固定（= L0 のみ）** |
| barge-in | 対象になる | `uses_inference` なら**推論を明け渡す** |

**Job が LLM / GPU を使うときは Arbiter から `inference_lease` を取る。** foreground が推論を要求したら lease は即座に revoke され、Job は `cooperative` に中断する。

これが無いと、Reflection Job の推論が会話の LLM 初トークンを待たせ、レイテンシ SLO を直撃する。

詳細 → [ADR-018](../decisions/ADR-018-foreground-and-jobs.md), [../architecture/agent.md](../architecture/agent.md)

---

## Tool Execution 状態機械

**Activity とは独立に遷移する。**

```
 authorized ──► bound ──► executing ──► confirmed
      │           │           │      └──► failed
      │           │           └──────────► unknown   （Core crash / abandoned）
      └──► denied └──► bind_failed
```

### 状態の意味

| 状態 | 意味 |
|---|---|
| `authorized` | Canonicalize + Policy 判断を通過した |
| `denied` | Policy に拒否された |
| `bound` | `Tool.bind()` が Handle を返し、`BindVerifier` が検証を通した |
| `bind_failed` | bind または verify に失敗した。**実行しない** |
| `executing` | `Tool.execute()` 実行中 |
| `confirmed` | 完了を確認した |
| `failed` | 異常終了した（結果は無い） |
| `unknown` | **intent はあるが confirm が無い。結果が不明** |

### `unknown` が発生する条件

1. Core がクラッシュした（→ [../architecture/recovery.md](../architecture/recovery.md)）
2. 親 Activity が `abandoned` になり、Tool の完了を待たなかった

**`unknown` は「失敗」ではない。** 成功したかもしれないし、していないかもしれない。この区別は Crash Recovery で重要になる。

### 遷移を実行できるのは誰か

**Tool Registry のみ。** Tool 自身は状態を持たない。

---

## 2つの状態の組み合わせ

**乖離は正当である。** 以下が「あり得る組み合わせ」の一覧。

| Activity | Tool | 意味 | 正当性 |
|---|---|---|---|
| `running` | `executing` | 通常の実行中 | ✓ |
| `running` | `confirmed` | ツールが終わり、Activity が続いている | ✓ |
| `cancelling` | `executing` | `cooperative` / `hard` な Tool の停止待ち | ✓ |
| `cancelled` | `confirmed` | 停止前に完了した。結果は破棄 | ✓ |
| **`abandoned`** | **`executing`** | **切り離した子が barge-in 後も動いている**（`non_cancellable`、または猶予時間内に止まらなかった `cooperative`） | **✓ 正当** |
| `abandoned` | `confirmed` | 結果は出たが Lumi の文脈に入らない | ✓ |
| `abandoned` | `unknown` | 切り離し後の追跡を諦めた | ✓ |
| `completed` | `unknown` | **ありえない。バグ** | ✗ |
| `completed` | `executing` | **ありえない。バグ** | ✗ |
| `cancelled` | `executing` | **ありえない**（`cancelled` は全子停止を意味する。この状況は `abandoned`） | ✗ |

### 規則

1. `Activity = abandoned` かつ `Tool = confirmed` の場合、結果は **`abandoned_result` として監査ログにのみ記録**され、Memory にも PromptContext にも入らない
2. `Activity = completed` は「全ての子 Tool が `confirmed` / `failed` / `denied` / `bind_failed` のいずれか」を含意する
3. `Tool = unknown` は Crash Recovery の対象

---

## Cancellation 契約

**すべての Tool は3つのうち1つを宣言しなければならない。**

| 契約 | 意味 | 例 | Arbiter の扱い |
|---|---|---|---|
| `cooperative` | cancel_token を定期的にチェックし、次のチェックポイントで安全に中断する | LLM ストリーム、ループ処理、ファイル走査 | cancel 後、猶予時間内の停止を期待 |
| `hard` | 外部から強制終了できる（プロセス kill、コネクション切断） | subprocess、HTTP リクエスト、Playwright 操作 | cancel 後、即座に強制終了 |
| `non_cancellable` | 開始したら完了まで止められない | 単一の GPU 推論、OS 入力の1イベント、DB トランザクション | **完了を待つか abandon するかを決める** |

### 帰結（Permission と連動）

| 規則 | 理由 |
|---|---|
| **副作用を持つ `non_cancellable` は `risk >= L3` に固定** | キャンセルできない副作用をユーザー確認なしに起こさせない |
| `non_cancellable` の実行時間には上限を設ける | 超えるなら設計を分割する |
| **TTS 再生の停止は `hard`** | barge-in の生命線。バッファのミュートで即座に無音化できることを実装要件とする |

### barge-in のときに何が起きるか

```
[VAD スレッド] speech-start（ミュート閾値）
  └─ playback.mute_flag.set()        ← 同期。Arbiter を経由しない。→ audio.md
                                        Activity 状態遷移ではないので Invariant 4 の対象外

[asyncio] arbiter.interrupt('user_speech')
  │
  ├─ 旧 foreground → interrupt_requested
  ├─ 新 conversation Activity → accepted
  ├─ _foreground を新に切り替え → 新 running / 旧 cancelling（background）
  │
  ├─ 旧の Activity ツリーを走査
  │    ├─ cooperative な子 → cancel_token.fire()、猶予時間内の停止を待つ
  │    ├─ hard な子        → 強制終了
  │    └─ non_cancellable な子 → 切り離し候補
  │
  ├─ まだ動いている子が無い → 旧 = cancelled
  └─ ある                    → 旧 = abandoned（Tool は動き続ける）
```

> **「音が止まる」と「Activity が止まる」は別の経路である。**
> 前者は VAD スレッドが同期的に行い、Arbiter を経由しない。後者は asyncio 側で行う。ユーザーが体感するのは前者。
> Invariant 4 の「interrupt は例外なく Arbiter を経由する」は **Activity の中断**についての規則であり、再生バッファのミュートには適用されない（[../architecture/audio.md](../architecture/audio.md), [ADR-018](../decisions/ADR-018-foreground-and-jobs.md)）。

**`InterruptResult` が「何が止まり、何が abandoned になったか」を返す。** これは Inspector に表示し、デバッグ可能にする。

---

## テスト

これらは **LLM を呼ばずにテストできなければならない。**

| # | テスト |
|---|---|
| 1 | `current()` が起動直後・全終了後・cancel 後のいずれでも Activity を返す |
| 1b | **`running` な Activity が同時に2つ存在しない**（全シナリオで） |
| 1c | **preempt の途中でも `current()` が一意に定まる**（旧 `cancelling` / 新 `running`） |
| 1d | **idle が、他の Activity が foreground の間 `suspended` になる** |
| 1e | **`suspended` を idle 以外の Activity が取れない** |
| 2 | idle Activity が `proposed` を経ずに `running` で生成される |
| 3 | idle への cancel が no-op である |
| 3b | **`DeferredQueue` の提案が TTL で破棄される** |
| 3c | **同一 `kind` × `intent` の deferred 提案が重複しない** |
| 4 | 禁止された組み合わせ（`completed` × `unknown` 等）が型または実行時に作れない |
| 5 | `non_cancellable` な子がいる Activity の interrupt が `abandoned` になる |
| 5b | **猶予時間内に止まらなかった `cooperative` な子でも `abandoned` になる**（待ち続けて barge-in を壊さない。警告は残す） |
| 6 | `abandoned` な Activity の子 Tool の結果が Memory / PromptContext に入らない |
| 7 | `abandoned_result` が監査ログに記録される |
| 8 | Activity の状態遷移が Attention Arbiter 以外から実行できない |
| 9 | Tool の状態遷移が Tool Registry 以外から実行できない |
| 10 | `InterruptResult` が停止した Tool と abandoned な Tool を正しく報告する |
