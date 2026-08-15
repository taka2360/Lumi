---
paths:
  - "core/lumi/agent/**/*.py"
  - "core/lumi/memory/**/*.py"
  - "core/lumi/permission/**/*.py"
  - "core/lumi/tools/**/*.py"
  - "core/lumi/providers/**/*.py"
---

# Provenance — 信頼の追跡

定義 → [contracts/provenance.md](../../docs/contracts/provenance.md), [ADR-011](../../docs/decisions/ADR-011-provenance-no-laundering.md)

## 2つの型を混同しない

| 型 | 用途 | 値 |
|---|---|---|
| `ProvenanceClass` | **ラベル。説明と監査のため** | `TRUSTED` / `UNTRUSTED` / `DERIVED` |
| `TrustLevel` | **Policy 判断用。join-semilattice** | `TRUSTED` / `TAINTED` |

```python
taint(DERIVED) == TAINTED          # derived も tainted
join(a, b) = TAINTED if either is TAINTED
```

**`derived` を `untrusted` より安全としない。** 攻撃者は要約を生き延びるペイロードを作れる。
3値にしているのはユーザーへの説明と監査のためで、**Policy はこの区別を見ない**。

`max_provenance` という名前を使わない（曖昧で必ず取り違える）。**`effective_trust`** を使う。

## LLM 出力は「入力から propagate する」

```python
llm_output.trust_level = propagate_trust(prompt_inputs)
```

- 入力が全て trusted（persona / ユーザー発話 / internal state）→ 出力も `TRUSTED`
- 入力に untrusted / derived が1つでもある → `DERIVED` = `TAINTED`

**一律 `UNTRUSTED` にしない。** 2ターン目以降が常に tainted になり、規則が判別力を失う。
**`is_raw_external=True` を LLM 出力に立てない。** LLM 出力は外界の観測ではなくプロンプトの関数。

## trust の3スコープ

```python
effective_trust = join(block_trust, history_trust, session_trust)
```

| スコープ | 中身 |
|---|---|
| `block_trust` | このターンの ContextBlock（ツール結果・記憶）の join |
| `history_trust` | Working Memory に載っている Turn の join。**`compact()` で要約に置換しても join は保存する** |
| `session_trust` | **sticky。一度 `TAINTED` になったらセッション終了まで戻らない** |

`session_trust` は **Working Memory ではなく Session に持たせる**。Working Memory は compact で減るが、これは減ってはならない。

## 昇格は1経路だけ

**`trust_level = TRUSTED` を書いてよいのは2箇所。**

1. ユーザーの直接入力ハンドラ（音声 / テキスト / UI 操作）— **初期付与**
2. `MemoryStore.confirm()`（記憶 UI のユーザー確認）— **唯一の昇格**

**自動昇格の実装を作らない。** grep とテストで検証する。

## 実装時

- 外部から取得した生データは `is_raw_external=True` → `UNTRUSTED`
- Tool の結果に provenance を付けるのは **Tool Registry**。Tool の自己申告を信じない
- untrusted / derived な ContextBlock はプロンプト内で**明示的に隔離する**（書式は provenance.md が定義）
- **隔離は防御の一枚に過ぎない。** 最終防衛線は Policy の強制昇格（`tainted` + 実効 L3 → `ask`）
- 迷ったら**汚染側に倒す**
