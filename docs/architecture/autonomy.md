# Autonomy — Drive System, AutonomyGate, AutonomyBudget

> 「自分から動くが、鬱陶しくない」を成立させる仕組み。

親: [DESIGN.md](../DESIGN.md) / 関連: [agent.md](agent.md), [world-state.md](world-state.md), [ADR-007](../decisions/ADR-007-drive-system.md)

---

## 1. 解決したい問題

### 問題1: `if idle > 5min then speak` は破綻する

- すぐ鬱陶しくなる
- 条件を足すたびに if 文が増え、相互作用が読めなくなる
- **テストできない**（「鬱陶しいか」を if 文の集合から判定できない）

### 問題2: LLM に「動くべきか」を聞くと自己強化する

```
「暇だから LLM に聞いてみよう」
  → LLM「暇なので話しかけましょう」
  → 話しかける
  → また暇になる
  → 「暇だから LLM に聞いてみよう」
```

LLM は聞かれれば答える。**「何もしない」という選択肢を選ばせにくい。**

### 解

> **「動くべきか」は決定論的コードで決める。LLM は「何をするか」だけ決める。**

これにより:
- 常時 LLM 推論しない（設計原則4）
- テスト可能になる
- チューニング可能になる（パラメータが数値だから）

---

## 2. Drive System

### Drive と State を型として分ける

**`rest` を Drive に混ぜない。** `rest` は「行動したい欲求」ではなく「抑制」であり、型が違う。

```python
class Drive(Enum):
    """行動を駆動する欲求。argmax 競争に参加する。0.0-1.0"""
    SOCIAL    = "social"      # 話したい
    CURIOSITY = "curiosity"   # 調べたい
    DUTY      = "duty"        # やるべき
    PLAY      = "play"        # 遊びたい

# 抑制側は InternalState に属する（world-state.md）
#   fatigue        疲労
#   rest_pressure  休息圧
```

### 各 Drive の増加要因

| Drive | ↑ する条件 | ↓ する条件 |
|---|---|---|
| `social` | 最終対話からの経過時間、ユーザー在席、未共有の出来事がある | 会話した、「うるさい」と言われた |
| `curiosity` | 会話中の未解決トピック、新しいアプリ/URL の観測 | 調べた、話題が変わった |
| `duty` | 期限接近タスク、ユーザーからの保留依頼 | タスク完了 |
| `play` | アイドル時間、直近の遊び体験の少なさ | 遊んだ |

### 実効値の計算

```python
def effective_drive(d: Drive) -> float:
    return (
        base_drive[d]
        * fatigue_modifier(internal.fatigue)          # 疲れていると全部下がる
        * quiet_modifier(internal.rest_pressure,      # 深夜は全部下がる
                         world.get("time.quiet_hours"))
        * budget_modifier(autonomy_budget)            # 予算が減ると全部下がる
    )
```

**抑制を「全 Drive への一律の乗数」として表現する。** これにより「休みたい欲求が argmax で勝って何もしない」という不自然な状態機械を避けられる。

---

## 3. Tick — LLM を呼ばない判定

```python
async def tick(self):    # 30秒ごと
    self.drives.update(world_snapshot, internal_state, memory_signals)

    scores = {d: effective_drive(d) for d in Drive}
    winner = max(scores, key=scores.get)

    if scores[winner] < THRESHOLD:
        return                                # ここで終わり。LLM を呼ばない

    if not self.gate.passes(winner, world, internal, budget):
        self.drives.penalize(winner)          # 通らなかった Drive を減衰
        return                                # ここで終わり。LLM を呼ばない

    result = self.arbiter.propose(Activity(
        kind=ActivityKind.AUTONOMOUS,
        actor=Actor.SELF_INITIATED,
        intent=winner,
        deferrable=True,
    ))

    if isinstance(result, Accepted):
        await self._generate_and_execute(result.activity, winner)
        #   ↑ ここで初めて LLM が「具体的に何をするか」を生成する
```

**大半の tick は LLM を呼ばずに終わる。** これが「常時 LLM 推論しない」の実装。

---

## 4. AutonomyGate

**全てを AND で通過する必要がある。**

| ゲート | 条件 | 理由 |
|---|---|---|
| 在席 | `world["user.present"] == True` | 不在時の発話は無意味 |
| 集中中でない | `user.activity_class` が `meeting` / `focused_work` でない | 邪魔をしない |
| 全画面でない | `desktop.fullscreen == False` | ゲーム中・動画視聴中に割り込まない |
| DND でない | 設定 | ユーザーの明示的意思 |
| Quiet Hours 外 | 設定 | 深夜に話しかけない |
| クールダウン | 同一 Drive 種別の最小間隔を超えている | 連発しない |
| **AutonomyBudget 内** | 後述 | 「鬱陶しさ」と「暴走コスト」の統一的な抑制 |
| Permission | 自律 actor で許される行為か | Phase 6 以降。Phase 3 は発話のみ |

### Gate が通らなかったとき

**Drive を penalize する**（少し減衰させる）。理由は、通らない Drive が閾値を超えたまま張り付くと、条件が変わった瞬間に発火することになり、不自然だから。

---

## 5. AutonomyBudget — 第一級オブジェクト

> **「鬱陶しさ」と「暴走コスト」は同じ問題である。** 単一の予算概念で両方を抑える。

```python
@dataclass
class AutonomyBudget:
    window_start: datetime
    window_duration: timedelta      # 例: 1時間

    max_interrupts: int             # 時間あたり最大割り込み回数
    max_tokens: int                 # 時間あたり LLM トークン
    max_wallclock: timedelta        # 時間あたり自律活動の総時間

    interrupts_used: int
    tokens_used: int
    wallclock_used: timedelta
```

### なぜ「割り込み回数」を予算にするのか

「1時間に何回話しかけてよいか」は、ユーザーが直感的に設定できる唯一の指標である。「Drive の閾値を 0.7 に」とは言えないが、「1時間に2回まで」とは言える。

### 「うるさい」フィードバックループ

ユーザーが「うるさい」と言った / ボタンを押したとき:

```
Signal(type="ui.user_said_noisy")  または STT 結果の意図判定
  ↓ Core が処理
  1. AutonomyBudget を即時消費（残りをゼロにする）
  2. 発火した Drive を強制減衰
  3. Memory に書き込む（assertion_mode=user_stated, salience 高）
  4. DomainEvent(stream_key="autonomy", type="AutonomyFeedbackReceived")
```

**3 が重要。** 記憶に残ることで、次回以降の Drive 更新に影響する。「この時間帯は嫌がられる」「この話題は歓迎されない」を学習できる（統計的にではなく、記憶として）。

---

## 6. Phase 3 は「発話のみ」

**Phase 3 では自律行動は発話に限定し、OS 操作をしない。**

理由: 自律の難しさは2つあり、独立した問題だから。

| 問題 | 解く Phase | 必要なもの |
|---|---|---|
| **鬱陶しさの調整** | Phase 3 | Drive / Gate / Budget。権限システムは不要 |
| **安全性** | Phase 6 | Permission Kernel、actor による昇格 |

同時にやると、問題が起きたときに「うるさいのか、危ないのか」の切り分けができない。

### Phase 3 の完了条件

> **1日つけっぱなしにして不快でない。**

これはベンチマークではなく**実際の同居体験**を品質基準にしている。このプロジェクトは「何ができるか」より「一緒にいて嫌じゃないか」の方が重要であるため。

**これが通らない限り Phase 6 に進まない。**

---

## 7. Phase 6 — 自律 actor によるツール使用

Phase 4a-4c（Permission）完了後、自律行動がツールを使えるようになる。

### actor による権限昇格

**Policy の定義は [permission.md](permission.md) の `decide()` が唯一。** ここでは自律側から見た帰結だけを述べる。

| | 自律行動でできること |
|---|---|
| L0 読み取り・観測 | できる |
| L1 ブラウザ閲覧・Web検索 | できる（AutonomyBudget の範囲内） |
| L2 ファイル読み取り・作業領域書き込み | **ユーザーに聞く** |
| L3 以上 | **できない**（昇格ではなく拒否） |

**「ユーザーが頼んだファイル読み取り」と「Lumi が勝手にするファイル読み取り」は別の行為である。**

**L3 以上は「厳しくなる」のではなく「できない」。** 自律行動にアプリ起動・入力インジェクション・削除をさせる設計にはしない。

### Agent Loop（Phase 6）

```
World State + Internal State + Memory
  ↓
Drive（発火判定）
  ↓
Goal Generation（LLM。何を達成したいか）
  ↓
Planning（LLM または決定論的。どうやるか）
  ↓
Permission（各ステップ。actor=self_initiated）
  ↓
Execution
  ↓
Observation
  ↓
Evaluation → Memory / Internal State に反映
```

**可視化が必須。** 「今何をしているか」が常に見えないと、自律エージェントはチューニング不能になる。

---

## 8. AIRI との比較

| | AIRI | Lumi |
|---|---|---|
| 自発の源泉 | **ユーザー登録タスクのリマインダのみ**（`orchestrator/store.ts` の2秒 tick） | Drive System（内発的動機） |
| 発火判定 | 期限到来のみ | 決定論的な Drive 計算 + Gate |
| 抑制 | なし | fatigue / quiet / budget の乗数 + Gate |
| 予算 | なし | AutonomyBudget（割り込み / トークン / 時間） |
| フィードバック | なし | 「うるさい」→ 予算消費 + Drive 減衰 + **記憶** |
| Mood | LLM 出力から毎ターン導出される瞬間値 | Internal State に持続。慣性と減衰 |
| 権限 | 自律と手動の区別なし | **actor による1段昇格** |

---

## 9. テスト

**これらは LLM を呼ばずにテストできなければならない。** 呼ばないとテストできないなら設計が間違っている。

| # | テスト |
|---|---|
| 1 | Drive の増加・減衰が入力に対して期待通り |
| 2 | `effective_drive` の乗数が正しく効く（fatigue / quiet / budget） |
| 3 | 閾値未満なら LLM を呼ばずに tick が終わる |
| 4 | Gate の各条件が単独で通過を阻止する |
| 5 | Gate 不通過時に Drive が penalize される |
| 6 | AutonomyBudget が正しく消費・リセットされる |
| 7 | 「うるさい」で予算がゼロになり Drive が減衰する |
| 8 | 「うるさい」が Memory に記録される |
| 9 | 会話中の自律提案が `Deferred` になる |
| 10 | Quiet Hours 中に発火しない |
| 11 | `actor=self_initiated` で L2 が `ask`、L3 以上が `deny` になる（Phase 6） |
| 12 | **シミュレーション: 24時間分の World State 系列を流し、割り込み回数が予算内に収まる** |

12 が「鬱陶しくないこと」の唯一の自動テスト。実際の体感は Phase 3 の完了条件で人間が判断する。
