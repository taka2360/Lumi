# ADR-013: Memory に assertion_mode を導入し、LLM 抽出結果を無条件に信用しない

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../architecture/memory.md](../architecture/memory.md), [../interfaces/memory.md](../interfaces/memory.md), [ADR-011](ADR-011-provenance-no-laundering.md) |

---

## Decision

`MemoryRecord` に **`assertion_mode`（どういう根拠か）** を必須フィールドとして持たせる。
これは **`confidence`（確からしさ）とは別軸**である。

```python
class AssertionMode(Enum):
    USER_CONFIRMED = "user_confirmed"   # ユーザーが記憶UIで確認した（最強）
    USER_STATED    = "user_stated"      # ユーザーが明示的に述べた
    INFERRED       = "inferred"         # 会話から推測した
    SELF_GENERATED = "self_generated"   # Lumi 自身の推測・想像
    EXTERNAL       = "external"         # 外部データ由来
```

加えて **`evidence_ref`**（根拠となる発話への参照）を持たせる。

強弱の順序（矛盾解決に使う）:
```
USER_CONFIRMED > USER_STATED > INFERRED > SELF_GENERATED > EXTERNAL
```

---

## Reason

### LLM は「言われたこと」の抽出は得意だが、「それが本気か」の判定は当てにならない

ユーザーが「冗談だけど、僕は火星人だよ」と言ったとき、素直に抽出するとこうなる。

```
user_species = "Martian"
confidence = 0.95
```

**`confidence` が高いのは間違っていない。** LLM は「ユーザーが確かにそう言った」ことに 0.95 の確信を持っている。

問題は、**「確かに言われた」と「それが事実である」を区別する軸が無い**ことである。

### `confidence` と `assertion_mode` は独立している

| | |
|---|---|
| `confidence` | その記憶の内容がどれくらい確からしいか |
| `assertion_mode` | **その記憶がどういう根拠で生まれたか** |

組み合わせが意味を持つ。

| assertion_mode | confidence | 意味 |
|---|---|---|
| `USER_STATED` | 0.95 | ユーザーがはっきりそう言った |
| `USER_STATED` | 0.3 | ユーザーがそう言ったが、冗談っぽかった |
| `SELF_GENERATED` | 0.9 | **Lumi が強く確信しているが、それは自分の推測** |
| `INFERRED` | 0.6 | 会話から何となくそう思う |

`SELF_GENERATED` × `confidence 0.9` が**最も危険な組み合わせ**であり、これを1つの数値では表現できない。

### 人格設計でもある

> **「わたしがそう思ってるだけかもだけど」と言えるAIと、幻覚を事実として断言するAIの差はここで決まる。**

自分の知識の出所と確からしさを区別できることは、人格の一部である。

これはセキュリティ（Invariant 3, 7）であると同時に、Lumi が「生き物っぽい」と感じられるかどうかの分かれ目でもある。

### AIRI には記憶自体が無い

AIRI の `memory_fragments` テーブルには `importance` / `emotional_impact` / `memory_type` があるが、**参照コードがゼロ**（未実装）。認識論的メタデータの概念も無い。

**Lumi は独自に設計する。**

---

## Alternatives

### A. `confidence` のみ

**利点:** フィールドが1つ。実装が単純
**欠点:** 「確かに言われた冗談」と「Lumi の強い思い込み」が同じ値になる。**火星人問題が解けない**

### B. LLM に「これは事実か冗談か」を判定させる

**利点:** フィールドが増えない
**欠点:** LLM の判定を信用することになる。**判定を間違えたら記録に残らない**。事後の訂正もできない

### C. ユーザー確認済みの記憶だけを保存する

**利点:** 最も安全
**欠点:** 使い物にならない。会話のたびに確認を求められる

### D. 記憶に階層を持たせる（確定 / 暫定）

**利点:** 単純な2値
**欠点:** 「Lumi の推測」と「会話からの推測」の区別がつかない。5値の方が実用的

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| フィールドが2つ増える | `assertion_mode` と `evidence_ref` |
| LLM 抽出時に追加の判定 | 「これはユーザーが述べたことか、推測か」を問う |
| プロンプトが長くなる | 提示方法が assertion_mode で変わる |

### LLM に判定させる部分は残る

`USER_STATED` か `INFERRED` かの判定は、結局 LLM が行う。

**それでも意味がある理由:**
- 判定が**記録に残る**ので、後から検証できる
- `evidence_ref` があるので、根拠に戻れる
- ユーザーが記憶 UI で訂正できる
- **間違いが `USER_CONFIRMED` に昇格することはない**（人間の確認が必要）

---

## Consequences

### プロンプトへの提示方法が変わる

| assertion_mode | 提示 |
|---|---|
| `USER_CONFIRMED` / `USER_STATED` | 事実として提示 |
| `INFERRED` | **「〜と思われる（会話から推測）」** |
| `SELF_GENERATED` | **「〜とわたしは思っている（根拠は自分の推測）」** |
| `EXTERNAL` | 出所を明記し、untrusted ブロックに入れる |

これにより Lumi が自分の知識の出所を口に出せるようになる。

### 矛盾解決に使われる

```
新しい MemoryCandidate と既存の semantic memory が矛盾
  ↓
assertion_mode を比較
  新しい方が強いか同等 → 既存を supersede
  新しい方が弱い       → 新しい方を confidence 低で保存（または破棄）
```

**`USER_CONFIRMED` な記憶を `SELF_GENERATED` が上書きすることはない。**

### 検索スコアの重みになる

| assertion_mode | `assertion_weight`〔Provisional〕 |
|---|---|
| `USER_CONFIRMED` | 1.2 |
| `USER_STATED` | 1.0 |
| `INFERRED` | 0.8 |
| `SELF_GENERATED` | 0.6 |
| `EXTERNAL` | 0.7 |

### `USER_CONFIRMED` が Invariant 7 の昇格経路になる

```
MemoryStore.confirm(id)
  → assertion_mode = USER_CONFIRMED
  → trust_level = TRUSTED       ← Invariant 7 の唯一の昇格経路
```

**`assertion_mode` と `provenance` が結合する点。** これにより記憶 UI が Phase 2 の必須機能になる。

### LLM 抽出プロンプトに判定を含める

Reflection Job の抽出プロンプトで、明示的に問う。

```
各記憶について、以下を判定してください:
- ユーザーが明示的に述べたことか、会話から推測したことか
- 冗談・仮定・引用・他者の発言の引用ではないか
- 根拠となる発話はどれか
```

**プロンプトは外出しする**（[DESIGN.md](../DESIGN.md) §8 の Provisional）。実運用で必ず調整が入る。

### `salience` の決定論的補正と組み合わせる

`assertion_mode` は「どういう根拠か」、`salience` は「どれくらい重要か」。

```python
salience = clamp(
    llm_salience        * 0.40
  + emotional_intensity * 0.20
  + novelty             * 0.15
  + explicit_marking    * 0.15    # 「覚えておいて」と言われた
  + repetition          * 0.10
)
```

**LLM の主観だけに任せない**という点で、`assertion_mode` と同じ思想。
