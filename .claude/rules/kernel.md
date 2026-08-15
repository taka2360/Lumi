---
paths:
  - "core/lumi/kernel/**/*.py"
  - "core/lumi/agent/**/*.py"
---

# Kernel — Attention Arbiter / Activity / Job

定義 → [contracts/state-machines.md](../../docs/contracts/state-machines.md), [architecture/agent.md](../../docs/architecture/agent.md), [ADR-016](../../docs/decisions/ADR-016-always-one-activity.md), [ADR-018](../../docs/decisions/ADR-018-foreground-and-jobs.md)

## foreground の定義（曖昧さを残さない）

```python
_foreground: ActivityId            # 常に有効。None にならない
_background: dict[ActivityId, Activity]   # cancelling / suspended
```

- **Invariant 4 の「ちょうど1つ」は `_foreground` 参照についての言明**
- **`running` は foreground だけが取る。** したがって `running` も常にちょうど1つ
- `Activity` オブジェクトが同時に複数存在するのは**違反ではない**（cancelling / suspended）
- **idle は「常に `running`」ではない。** 他が foreground の間は `suspended`
- `current()` は `None` を返さない

## preempt の遷移順序 — この順序を守る

```
1. 旧 foreground → interrupt_requested
2. 即時効果（TTS ミュート等）        ← Activity 状態遷移ではない
3. 新 Activity → accepted
4. _foreground を新に切り替える      ★ ここで Invariant 4 が満たされ続ける
5. 新 → running / 旧 → cancelling（background へ）
6. 旧は background で子 Tool の停止を待つ
```

**4 より前に旧を `cancelling` にしない。** 一瞬 `running` が0個になり Invariant 4 が破れる。
**子 Tool の停止を待ってから 4 をしない。** barge-in が遅れる。

## Job は Activity ではない

Reflection / 再埋め込み / DB メンテナンスは `Job`。

| 規則 | 内容 |
|---|---|
| foreground | **取らない** |
| `actor` | **`system` 固定 → L0 のみ。** L1 以上が要るならそれは Activity として propose すべき仕事 |
| 推論 | `uses_inference` なら **`arbiter.inference_lease()` を取る** |
| revoke | foreground が推論を要求したら**即座に revoke**。Job は `cooperative` に中断し、進捗を捨てて後で再開 |

**Job を Arbiter の管理外で LLM / GPU を使わせない。** barge-in が効かなくなり SLO を直撃する。

## Cancellation

3契約（`cooperative` / `hard` / `non_cancellable`）を全 Tool が宣言する。

- `cancel_token.fire()` で全部止まるという仮定は**誤り**
- `non_cancellable` な子が残ったら Activity は `abandoned`。その結果は Memory にも PromptContext にも**入れない**（監査ログにのみ `abandoned_result` として記録）
- `interrupt()` は **`InterruptResult`**（何が止まり、何が abandoned になったか）を返す。Inspector に出す

## DeferredQueue

- 保持するのは `ActivityProposal`（Activity そのものではない）
- **TTL を持たせる**（既定 10 分）。30分前の「話しかけたかったこと」を今実行しない
- 同一 `kind` × `intent` は1件のみ（新しい方で置換）

## 実装チェック

- [ ] `running` な Activity が同時に2つ存在しない（preempt の途中を含む）
- [ ] Activity の状態遷移が Arbiter 以外から実行できない
- [ ] `_foreground` への代入が Arbiter 以外に存在しない
- [ ] Job が foreground を取らない
- [ ] Job が L1 以上のツールを呼べない
