# ADR-018: foreground を単一の参照として定義し、Job を Activity と分離する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-15 |
| 関連 | [ADR-016](ADR-016-always-one-activity.md), [../contracts/state-machines.md](../contracts/state-machines.md), [../architecture/agent.md](../architecture/agent.md), [../contracts/invariants.md](../contracts/invariants.md) |
| 補完する ADR | [ADR-016](ADR-016-always-one-activity.md)（idle Activity の状態を修正する） |

---

## Decision

### 1. `foreground` を単一の参照として定義する

```python
class AttentionArbiter:
    _foreground: ActivityId      # 常に有効な1つ。None にならない
    _background: dict[ActivityId, Activity]   # cancelling / suspended なもの
    _deferred: DeferredQueue

    def current(self) -> Activity:
        return self._activities[self._foreground]
```

> **Invariant 4 の「常にちょうど1つ」は `_foreground` についての言明である。**
> `Activity` オブジェクトが同時に複数存在することは違反ではない。

### 2. `running` は foreground だけが取る状態にする

**ADR-016 の「idle は常に `running`」を修正する。**

| 状況 | idle Activity の状態 |
|---|---|
| 他に Activity が無い | `running`（= foreground） |
| 他の Activity が foreground | **`suspended`** |

`suspended` を Activity 状態機械に追加する。idle 以外の Activity は `suspended` を取らない。

### 3. preempt の遷移順序を確定する

```
1. 旧 foreground → interrupt_requested
2. TTS ミュート等の即時効果（[B-1] の critical path）
3. 新 Activity → accepted
4. _foreground を新に切り替える          ★ ここで Invariant 4 が満たされ続ける
5. 新 → running
6. 旧は background で cancelling を継続
     猶予時間内に全子が停止 → cancelled
     non_cancellable な子が残る → abandoned
```

**`cancelling` な Activity が background に存在することは Invariant 4 に違反しない。** foreground は常に1つだから。

### 4. `Job` を第一級概念とし、Activity と分離する

```python
@dataclass
class Job:
    id: JobId
    kind: JobKind                # reflection | maintenance | reembedding | ...
    actor: Actor                 # 常に system
    cancellation: Cancellation
    cancel_token: CancelToken
    uses_inference: bool         # LLM / GPU を使うか
```

| | **Activity** | **Job** |
|---|---|---|
| foreground を取る | **取る**（1つだけ） | **取らない** |
| 発話する | する | **しない** |
| barge-in の対象 | **なる** | `uses_inference` なら**推論を明け渡す** |
| actor | user_initiated / self_initiated / scheduled / system | **常に `system`（= L0 のみ）** |
| 例 | 会話、自律行動、タスク、ゲーム、idle | Reflection Job、再埋め込み、DB メンテナンス |

### 5. `uses_inference = True` の Job は Arbiter に推論権を要求する

```python
async with arbiter.inference_lease(job) as lease:
    async for ev in llm.stream(..., cancel_token=lease.token):
        ...
```

**foreground Activity が推論を要求したら、lease は即座に revoke される**（`lease.token` が fire する）。Job は `cooperative` として中断し、後で再開する。

Job は Activity を `propose` できる（例: Reflection の結果を話題にしたい）が、**Job 自身が foreground になることはない。**

### 6. `deferred` な提案の所有者を定める

`DeferredQueue` を Arbiter が所有する。

| 項目 | 規則 |
|---|---|
| 保持 | `ActivityProposal`（Activity そのものではない） |
| TTL | 提案ごとに持つ。既定 10 分〔Provisional〕 |
| 再提案 | foreground が idle に戻ったとき、および `retry_after` 経過時 |
| 上限 | 同一 `kind` × `intent` は1件のみ（新しい方で置換） |
| 破棄 | TTL 超過、または関連する Drive が閾値を下回ったとき |

**古い提案が生き残って不意に発火することを防ぐ。** 「30分前に話しかけたかったこと」を今実行されるのは不気味である。

---

## Reason

### ADR-016 の記述が Invariant 4 と字面で矛盾していた

ADR-016 は idle の状態を「**常に `running`**」と定めた。会話 Activity も `running` になるため、**`running` が2つ**存在する。

ADR-016 自身が「『ちょうど1つ』と『0または1』は違う。決めなければ実装時に必ず解釈が割れる」と書いている以上、**「foreground とは何か」も同じ厳密さで定義しなければならない。**

`_foreground` を単一の参照として定義し、`running` を foreground に限定することで、Invariant 4 が字面どおり成立する。

### preempt には必ず窓が開く

`agent.md` の当初の `propose` は次のようになっていた。

```python
self.interrupt(reason=...)          # cooperative な子の停止を「待つ」
return Accepted(self._start(p))     # 待たずに新 Activity を開始
```

`cancelling` は「子 Tool の停止を待っている」状態なので、**旧が `cancelling` の間に新が `running` になる窓**が必ず存在する。barge-in の速度を優先する以上この窓は正しいが、**そのとき `current()` が何を返すか**が未定義だった。

`_foreground` の切り替えを遷移順序の4番目に固定することで、窓の間も `current()` が一意に定まる。

### Job が Arbiter の管理外にあると barge-in が破れる

ADR-016 は末尾で「バックグラウンド処理は Activity ではなく Job として扱う」と書きつつ、本文では「idle 中に Reflection Job が走る → `activity_id` が必要」と書いており、**同一 ADR 内で位置づけが割れていた。**

実害は具体的である。Reflection Job は **LLM を呼ぶ**（[memory.md](../architecture/memory.md)）。

```
Reflection Job が LLM で記憶を抽出中（GPU 占有）
  → ユーザーが話しかける
  → Job は Arbiter の管理外なので止まらない
  → 会話の LLM 初トークンが Reflection の完了待ちになる
  → p95 2.0 秒 の SLO を直撃する
```

`inference_lease` により、**推論資源の調停が Arbiter の責務として明示される。**

### Job に `actor` が要る

Job もツールを呼びうる（再埋め込みは `memory` lane を触る）。`actor` が無いと Policy が判断できない。

**Job の `actor` は常に `system` に固定する**（= L0 のみ）。Job が L1 以上を必要とするなら、それは Job ではなく Activity として `propose` すべき仕事である。この制約が「Job が勝手に副作用を起こす」ことを構造的に防ぐ。

---

## Alternatives

### A. Job も Activity にする

**利点:** 概念が1つ。`activity_id` が自然に埋まる
**欠点:** **Invariant 4 が壊れる。** Reflection 中は foreground が Reflection になり、「Lumi は今なにをしているか」が「記憶の整理」になる。会話が来たら preempt されるが、Reflection は会話と**同時に**進んでよい処理である。同時実行を認めた瞬間に Invariant 4 の意味が失われる

### B. Job を Arbiter の外に置いたままにする（当初案）

**利点:** 実装が単純
**欠点:** GPU / LLM の競合が調停されない。barge-in が Job に効かない。Job のツール呼び出しの `actor` が未定義

### C. Job も foreground を取るが、優先度を最低にする

**利点:** 機構が1つ
**欠点:** A と同じ。加えて「Job 実行中は idle でない」ことになり、自律の発火条件（idle 中に propose）が壊れる

### D. `running` を複数許し、foreground フラグを別に持つ

**利点:** idle の状態を変えなくて済む
**欠点:** 「`running` が複数ある」状態を許すと、Invariant 4 の検査が状態機械ではなくフラグの整合性検査になる。**型で保証できなくなる**

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| Activity 状態が1つ増える（`suspended`） | idle 専用の状態 |
| `inference_lease` の実装 | Arbiter が推論資源を知る（Phase 5 の `ModelResourceManager` と接続する窓口になる） |
| Job という概念の追加 | 実装者が Activity と Job を区別する必要がある |
| `DeferredQueue` の TTL 管理 | 提案の寿命管理 |

### 得るもの

- **Invariant 4 が字面どおり成立する**（解釈の余地が無くなる）
- barge-in が Reflection Job にも効く
- Job のツール呼び出しの Policy 判断が定義される
- deferred な提案が「いつ消えるか」が決まる

---

## Consequences

### Invariant 4 の文面を精密化する

> 同時に **foreground** である Activity は常にちょうど1つ。`_foreground` 参照がその唯一の所有者である。
> ユーザー入力による **Activity の中断**は例外なく Attention Arbiter を経由する。
> ただし**再生バッファのミュートは Activity 状態遷移ではない**（[audio.md](../architecture/audio.md) の critical path）。Arbiter を経由せずに発生してよい唯一の即時効果である。

最後の1行が無いと、[ADR-003](ADR-003-audio-in-core.md)（コールバック内で同期的にミュートする）が Invariant 4 違反に見える。

### `inference_lease` が Phase 5 への接続点になる

`ModelResourceManager`（Phase 5）は「どのモデルが VRAM に載るか」を管理する。`inference_lease` は「誰が今推論してよいか」を管理する。**両者は別の資源であり、後者は Phase 1 から必要**（Reflection は Phase 2 で登場する）。

### テスト項目

| # | テスト |
|---|---|
| 1 | idle が、他の Activity が foreground の間 `suspended` になる |
| 2 | preempt 中に `current()` が一意に定まる（旧 `cancelling` / 新 `running`） |
| 3 | `running` な Activity が同時に2つ存在しない |
| 4 | Job が foreground を取らない |
| 5 | foreground が推論を要求すると Job の `inference_lease` が revoke される |
| 6 | revoke された Job が `cooperative` に中断し、後で再開する |
| 7 | Job の `actor` が `system` 固定であり、L1 以上のツールを呼べない |
| 8 | `DeferredQueue` の提案が TTL で破棄される |
| 9 | 同一 `kind` × `intent` の deferred 提案が重複しない |
