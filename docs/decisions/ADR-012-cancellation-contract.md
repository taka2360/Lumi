# ADR-012: Cancellation を3段階の契約とし、Activity と Tool の状態を独立させる

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../contracts/state-machines.md](../contracts/state-machines.md), [../architecture/agent.md](../architecture/agent.md) |

---

## Decision

### 1. 全ての Tool が Cancellation 契約を宣言する

| 契約 | 意味 | 例 |
|---|---|---|
| `cooperative` | cancel_token を定期チェックし、次のチェックポイントで中断 | LLM ストリーム、ループ、ファイル走査 |
| `hard` | 外部から強制終了できる | subprocess、HTTP、Playwright |
| `non_cancellable` | 開始したら止められない | 単一 GPU 推論、OS 入力の1イベント、DB トランザクション |

### 2. Activity と Tool の状態機械を独立させる

**`Activity = abandoned` かつ `Tool = executing` は正当な状態。**

### 3. Permission と連動させる

> **副作用を持つ `non_cancellable` は `risk >= L3` に固定。**（起動時に fail-closed で検証）

---

## Reason

### `cancel_token.fire()` で全部止まるという仮定は誤り

```
ユーザーが barge-in
  → Activity は「終わった」ことにしたい（新しい発話に応えたいから）
  → しかし実行中の GPU 推論は止められない
```

subprocess・HTTP・Playwright・GPU 推論・OS 入力インジェクションは、**キャンセル方式も、そもそも中断可能かも異なる**。

### 2 が必要な理由 — 状態を混同すると表現できない

Activity の状態と Tool の状態を同じものとして扱うと、上記の状況が表現できない。

| Activity | Tool | 意味 |
|---|---|---|
| `abandoned` | `executing` | **`non_cancellable` が barge-in 後も動いている。正当** |
| `abandoned` | `confirmed` | 結果は出たが Lumi の文脈に入らない |
| `completed` | `unknown` | **ありえない。バグ** |

`abandoned` を正当な状態として定義することで、「止めたつもりが止まっていない」という暗黙の状態がなくなる。

### 3 が必要な理由 — キャンセルできない副作用

`non_cancellable` かつ副作用があるツールは、**一度始めたらユーザーが止められない**。

これをユーザー確認なしに実行させるのは危険すぎる。L3 以上に固定することで、必ず `ask` を経る。

**起動時に fail-closed で検証する**（`non_cancellable` + `side_effect != none` + `risk < L3` の Tool は登録できない）。

### barge-in が中核要件だから必要

Lumi の差別化点が barge-in である以上、「何がどう止まるか」を曖昧にできない。

AIRI は `stopAll('new-message')` という単一の粒度しか持たない。それで足りているのは、AIRI が barge-in を実装していないため。

---

## Alternatives

### A. 全ての Tool を `cooperative` と仮定する

**利点:** 実装が単純。契約の宣言が不要
**欠点:** GPU 推論や OS 入力は実際には止まらない。**「止まったはず」が嘘になる**

### B. 全ての Tool にタイムアウトだけを課す

**利点:** 単純
**欠点:** barge-in で即座に止まらない。「あと3秒待ってから止まります」では体感が壊れる

### C. Activity と Tool の状態を1つにする

**利点:** 概念が1つ減る
**欠点:** `non_cancellable` の状況が表現できない。「Activity は cancelled だが Tool は動いている」を「バグ」として扱うことになり、実際には正常な状況を異常扱いする

### D. `non_cancellable` を禁止する

**利点:** 全部止まることが保証される
**欠点:** **GPU 推論も OS 入力も実装できない。** 現実的でない

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| 全 Tool に契約の宣言が要る | メタデータが1つ増える |
| 状態機械が2つ | Activity と Tool を別々に管理する |
| `abandoned` の後始末 | 結果を捨てる処理、監査ログへの記録 |
| `non_cancellable` の L3 固定 | 一部のツールが必ず確認を経る |

### 得るもの

- **barge-in で「何が止まり、何が止まらないか」が明確になる**
- キャンセルできない副作用がユーザー確認なしに起きない
- 実装者が「このツールは止まるのか?」を考える強制力

---

## Consequences

### `InterruptResult` を返す

```python
def interrupt(self, reason: str) -> InterruptResult:
    """何が止まり、何が abandoned になったかを返す。"""
```

**Inspector に表示してデバッグ可能にする。** 「barge-in したのに何か動いている」が可視化される。

### TTS 再生の停止は `hard`

**barge-in の生命線。** バッファのミュートで即座に無音化できることを実装要件とする。

```python
def _audio_callback(self, indata, outdata, frames, ...):
    if self.playback.mute_flag.is_set():
        outdata[:] = 0     # 即座に無音
```

これは `hard` の中でも最速の部類（プロセス kill やコネクション切断すら不要）。

### `abandoned_result` の扱い

```
Activity = abandoned, Tool = confirmed
  → 結果は abandoned_result として監査ログにのみ記録
  → Memory にも PromptContext にも入らない
```

**ユーザーは既に別の話をしているため。** ただし副作用があった場合の通知は Phase 4 で検討する。

### `unknown` 状態が Crash Recovery に繋がる

`Tool = unknown`（intent はあるが confirm が無い）は2つの原因で発生する。

1. Core がクラッシュした
2. `abandoned` で完了を待たなかった

**どちらも「結果が不明」であり、Crash Recovery の対象になる**（[../architecture/recovery.md](../architecture/recovery.md)）。

### `non_cancellable` に実行時間の上限を設ける

止められない以上、長時間実行されると barge-in が事実上機能しなくなる。

**上限を超えるなら設計を分割する。** 例: 大きなファイルの書き込みを1回の `non_cancellable` にせず、チャンク単位の `cooperative` にする。

### 状態遷移の権限が明確になる

| 状態機械 | 遷移を実行できるのは |
|---|---|
| Activity | **Attention Arbiter のみ** |
| Tool | **Tool Registry のみ** |

これを静的検査とテストで保証する。
