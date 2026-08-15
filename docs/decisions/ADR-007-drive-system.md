# ADR-007: 自律行動を Drive System + AutonomyBudget で制御する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../architecture/autonomy.md](../architecture/autonomy.md), [../architecture/world-state.md](../architecture/world-state.md) |

---

## Decision

自律行動の発火判定を、**決定論的な数値計算（Drive System）**で行う。

> **「動くべきか」は決定論的コードで決める。LLM は「何をするか」だけ決める。**

```python
tick(30s):                          # LLM を呼ばない
    drives.update(world, internal, memory_signals)
    winner = argmax(effective_drive)
    if winner < THRESHOLD: return                    # 大半はここで終わる
    if not gate.passes(winner, ...): penalize; return
    arbiter.propose(...)
        ↓ Accepted
    LLM が「具体的に何をするか」を生成              # ここで初めて LLM
```

加えて **AutonomyBudget を第一級オブジェクト**とし、時間あたりの割り込み回数・トークン・wall-clock を明示的に管理する。

`rest` を Drive に含めず、**抑制は全 Drive への乗数**として表現する。

---

## Reason

### `if idle > 5min then speak` は破綻する

- すぐ鬱陶しくなる
- 条件を足すたびに if 文が増え、相互作用が読めなくなる
- **テストできない**（「鬱陶しいか」を if 文の集合から判定できない）

### LLM に「動くべきか」を聞くと自己強化する

```
「暇だから LLM に聞いてみよう」
  → LLM「暇なので話しかけましょう」
  → 話しかける → また暇になる → 繰り返し
```

**LLM は聞かれれば答える。「何もしない」を選ばせにくい。**

### 決定論的にすることで得られるもの

| | 内容 |
|---|---|
| **テスト可能** | 24時間分の World State 系列を流して、割り込み回数を検証できる |
| **チューニング可能** | パラメータが数値なので、設定ファイルで調整できる |
| **説明可能** | 「なぜ今話しかけたか」を Drive の内訳で説明できる（Inspector） |
| **コストゼロ** | 大半の tick で LLM を呼ばない（設計原則4） |

### `rest` を Drive に混ぜない理由

`rest` は「行動したい欲求」ではなく「抑制」であり、**型が違う**。

argmax の競争に混ぜると「休みたい欲求が勝って何もしない」という不自然な状態機械になる。

```python
# ✓ 正しい表現
effective_drive(d) = base_drive[d]
                   * fatigue_modifier(fatigue)
                   * quiet_modifier(rest_pressure, quiet_hours)
                   * budget_modifier(budget)
```

抑制を**全 Drive への一律の乗数**とすることで、拡張しやすくなる。

### AutonomyBudget を第一級にする理由

> **「鬱陶しさ」と「暴走コスト」は同じ問題である。**

- 話しかけすぎ → 鬱陶しい
- ツールを使いすぎ → 計算資源と時間の浪費

両方を「時間あたりの予算」という単一概念で抑えられる。

「1時間に何回話しかけてよいか」は**ユーザーが直感的に設定できる唯一の指標**でもある。「Drive の閾値を 0.7 に」とは言えないが「1時間に2回まで」とは言える。

---

## Alternatives

### A. ルールベース（if 文の集合）

**利点:** 実装が単純。初期は動く
**欠点:** 条件が増えると相互作用が読めない。テストできない。チューニングが「if 文を書き換える」になる

### B. LLM に毎回判断させる

**利点:** 柔軟。文脈を考慮できる
**欠点:** 自己強化ループ。常時 LLM 推論（設計原則4に反する）。コスト。**判断がテストできない**

### C. 強化学習

**利点:** ユーザーの反応から学習できる
**欠点:** 学習データが集まらない（単一ユーザー）。デバッグ不能。**「なぜ今話しかけたか」が説明できない**。学習基盤は非目標

### D. スケジュールのみ（AIRI の実質的な状態）

**利点:** 予測可能。実装が最も単純
**欠点:** **内発的動機が無い。** 「PC という世界に住んでいる AI 生命体」にならない。AIRI の `orchestrator/store.ts` は2秒 tick を回しているが、実際にはユーザー登録タスクのリマインダ配送のみ

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 | 緩和 |
|---|---|---|
| **パラメータ調整が必要** | Drive の増減率、閾値、乗数、τ | 設定ファイル化。Phase 3 で実運用しながら調整 |
| モデルの妥当性が未検証 | 4つの Drive が適切かは仮説 | Phase 3 の完了条件で人間が判断 |
| 実装が if 文より複雑 | 状態を持つ数値モデル | テストで担保 |

### 得るもの

- テスト可能・チューニング可能・説明可能
- 常時 LLM 推論しない
- 「鬱陶しさ」を数値で管理できる

---

## Consequences

### Phase 3 を「発話のみ」にする

自律の難しさは2つあり、**独立した問題**である。

| 問題 | 解く Phase | 必要なもの |
|---|---|---|
| **鬱陶しさの調整** | Phase 3 | Drive / Gate / Budget。権限システムは不要 |
| **安全性** | Phase 6 | Permission Kernel、actor による昇格 |

同時にやると、問題が起きたときに「うるさいのか、危ないのか」の切り分けができない。

### Phase 3 の完了条件が体験ベースになる

> **1日つけっぱなしにして不快でない。**

ベンチマークではなく実際の同居体験を品質基準にする。このプロジェクトは「何ができるか」より「一緒にいて嫌じゃないか」の方が重要であるため。

**これが通らない限り Phase 6 に進まない。**

### Internal State が必要になる

`fatigue` / `rest_pressure` / `mood` / `drives` は World State（TTL で失効する観測）ではなく Internal State に属する（[ADR-014](ADR-014-world-vs-internal-state.md)）。

### 「うるさい」フィードバックが記憶に残る

```
ユーザーが「うるさい」
  → AutonomyBudget を即時消費
  → Drive を強制減衰
  → Memory に書き込む（assertion_mode=user_stated, salience 高）
```

**3つ目が重要。** 記憶に残ることで、次回以降の Drive 更新に影響する。「この時間帯は嫌がられる」を学習できる（統計的にではなく、記憶として）。

### Inspector に Drive の内訳が要る

**「なぜ今それを言ったのか」を後から追跡できることは設計要件。**

```
social: base=0.72 × fatigue(0.9) × quiet(1.0) × budget(0.5) = 0.32  → 閾値未満
```

これが無いと Phase 6 でチューニング不能になる。

### パラメータは設定ファイルに外出しする

Drive の種類・増減率・閾値・乗数は**必ず調整が入る**。コードにハードコードしない（[DESIGN.md](../DESIGN.md) §8 の Provisional）。
