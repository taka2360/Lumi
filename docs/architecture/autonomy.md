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
                         clock.quiet_hours())          # facet ではなく導出値（ADR-050）
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

### ★ dry-run（Phase 3d）が抑止するのは、この最後の1行だけ〔2026-09-06〕

**`_generate_and_execute()` を丸ごと飛ばしてはいけない。** そこには LLM 生成と発話のほかに、
**予算の消費・cooldown の設定・Drive の減衰**が入っている。飛ばすと、
**通ったのに何も減らない**——Drive は閾値の上に留まり、**次の tick でまた通る。**

| | dry-run で | なぜ |
|---|---|---|
| Gate 判定 | **本番と同じ** | 測りたいものそのもの |
| `propose()` | **本番と同じ**（呼ぶ） | 呼ばないと Arbiter の `Deferred` / `Rejected` が観測できない——**それも測りたい判定である** |
| **`complete()`** | **必ず呼ぶ**（下記） | **呼ばないと foreground が自律 Activity のまま張り付き、以降の提案が全部 `Deferred` になる** |
| **DomainEvent** | **shadow と分かる形で出す**（下記） | そのまま出すと、**喋っていない発話が「起きた事実」として 30 日残る** |
| `interrupts_used` | **消費する** | 消費しないと、予算が効いている様子が観測できない |
| **`tokens_used`** | **見積もりを計上する**（下記） | **生成しないと 0 のまま**。本番はここで先に予算が尽きることがある |
| **`wallclock_used`** | **見積もりを計上する**（下記） | 同上 |
| cooldown | **張る** | 張らないと、同じ Drive が連発する様子しか見えない |
| Drive の減衰 | **減衰させる** | 減らないと、閾値超えが張り付く |
| **LLM 生成** | **しない** | 抑止したいのはここ |
| **発話** | **しない** | 同上 |

#### ★ shadow Activity を完了させる。**さもないと2回目以降が起きない**

`propose()` が `Accepted` を返した時点で、**その Activity はもう `running` の foreground であり、
idle は `suspended` になっている**（[../contracts/state-machines.md](../contracts/state-machines.md)）。
**idle に戻すのは `complete()` だけ**である。

本番ではその `complete()` が `_generate_and_execute()` の中にある。
**dry-run が生成を飛ばすついでに完了も飛ばすと、最初に通った1回で foreground が固まる。**

| そのあと何が起きるか | |
|---|---|
| 次の tick 以降 | `deferrable=True` の提案は**全部 `Deferred`**。自律発話が**二度と起きない** |
| ログ | 「1日に1回しか話しかけようとしなかった」と出る。**実際には Gate を評価する前に詰まっている** |
| Invariant 4 | **破れない。** foreground はちょうど1つのまま——**だから静かに壊れる** |

**これは前項（状態を進めない）と正反対の壊れ方であり、両方同時に成立する。**
判定だけ記録すれば頻度が多すぎ、完了を忘れれば頻度が 1 回で止まる。
**どちらも「3d のログを見て Gate を決める」を無効にする。**

> **`complete()` は記録の後に呼ぶ。** 予算・cooldown・Drive を更新してから完了させる。
> 順序を逆にすると、完了で idle に戻った直後に次の tick が走り、**まだ減っていない Drive を見る。**

**受け入れる差**: shadow Activity は**発話の長さのあいだ foreground を占有しない**（即座に完了する）。
本番では数秒間ほかの提案を締め出す。**cooldown が分単位であるのに対し秒単位なので、
頻度のログは歪まないと判断した**〔Provisional〕。
**3d で「shadow 完了の直後に別の Drive の提案が固まって通る」現象が見えたら、この判断を見直す。**

#### ★ shadow の DomainEvent を本物と区別する

`propose()` / `complete()` は `ActivityStarted` / `ActivityEnded` を**発行する**
（`_publish_started` / `_publish_ended`）。dry-run で本番と同じ経路を通す以上、
**抑止した試行のぶんだけ「実際に起きた活動」が記録される。**

| どこに出るか | 何が困るか |
|---|---|
| イベント DB（既定 30 日 → [../contracts/privacy.md](../contracts/privacy.md) §2 の行 5） | **喋っていない発話が「起きた事実」として残る** |
| Inspector | 数日ぶんの偽の活動履歴。**3d のログを読む作業そのものを汚す** |
| 将来の購読者 | Phase 6 以降、`ActivityEnded` を見て動くものが出たときに**誤作動する** |

**`Activity` に shadow の印を持たせ、DomainEvent の payload に載せる。**
**イベントを出さない選択はしない**——出さないと「Arbiter がどう判定したか」の履歴も消え、
**dry-run の目的そのものが失われる。** 区別できる形で残す。

> **Invariant 6 は破らない。** DomainEvent を発行するのは Core のままで、
> **増やすのは payload の1フィールドだけ**である。

#### ★ 予算は3次元ある。1つだけ消費しても本番にならない

`AutonomyBudget` は `max_interrupts` / `max_tokens` / `max_wallclock` の3つを持つ（§5）。
**本番では、割り込み回数より先にトークンや時間で尽きることがある。**
`interrupts_used` だけ進める dry-run は、**その場合に本番より長く Gate が開き続け、
`budget_modifier` も高いままになる**——「本番と同じ状態遷移」という前提が崩れる。

**生成しないのだから実測は取れない。決定論的な見積もりを計上する。**

| | |
|---|---|
| `tokens_used` | **prompt は実際に組み立てて数え、completion だけ定数で見積もる**〔Provisional〕。[measurements/phase2.md](../measurements/phase2.md) の実測（生成 18〜24 トークン）を出発点にする。数え方の定義は §5 |
| `wallclock_used` | 同じく定数。**`critical_path_ms` の実測**（p50 1.17 s）＋発話時間の見積もり |
| **見積もりであることを記録する** | dry-run のログに「推定値」と印を付ける。**3e で実測に置き換わったとき、両者を比べられるようにする** |

> **見積もりが外れる可能性は残る。** それでも 0 を計上するよりは近い——
> **0 は「この次元の予算は存在しない」と主張することと同じ**である。

**この区別を間違えると、3d のログは 3e の予測にならない。**
「1日に何回話しかけようとしたか」が実際より桁で多く出て、**Gate を過剰に締めることになる。**

---

## 4. AutonomyGate

**全てを AND で通過する必要がある。**

| ゲート | 条件 | 理由 |
|---|---|---|
| 在席 | `world["user.present"] is True` | 不在時の発話は無意味。**`Unknown` は通さない**（下記） |
| 集中中でない | `user.activity_class` が**割り込んでよい値の集合に入っている**（**許可リスト**。下記） | 邪魔をしない。**期限切れも分類失敗も通さない** |
| 全画面でない | `desktop.fullscreen is False` | ゲーム中・動画視聴中に割り込まない。**`Unknown` は通さない** |
| DND でない | 設定 | ユーザーの明示的意思 |
| Quiet Hours 外 | 設定 | 深夜に話しかけない |
| クールダウン | 同一 Drive 種別の最小間隔を超えている | 連発しない |
| **AutonomyBudget 内** | 後述 | 「鬱陶しさ」と「暴走コスト」の統一的な抑制 |
| Permission | 自律 actor で許される行為か | Phase 6 以降。Phase 3 は発話のみ |

### ★ 「知らない」は「条件を満たしている」ではない〔2026-09-06〕

**「`meeting` でない」は、知らないときにも真になる。** 否定形で書いた条件は fail-open である。

#### 「知らない」は2種類あり、**どちらも通してはいけない**

| | 何が起きたか | 表現 |
|---|---|---|
| **facet が無い** | 観測が届いていない / 期限切れ（Sensor が黙った） | **`Unknown`**（facet 層の sentinel。[world-state.md](world-state.md) §2） |
| **分類できない** | 観測はある（前面アプリは分かる）が、**それが何をしている状態か判らない** | **`unknown`**（`user.activity_class` の enum 値。同 §3） |

**この2つは別物であり、片方だけを弾く実装は必ずもう片方で漏れる。**
`cls is Unknown` だけ見ると、**`unknown` は「既知の値で、`meeting` でも `focused_work` でもない」ので通る。**

> **enum の `unknown` を消して sentinel に寄せることはしない。** 2つは違う質問に答えている——
> 「Sensor が生きているか」と「このアプリが何なのか分かるか」である。
> **3d の dry-run で「なぜ発火しなかったか」を読むとき、この区別が要る**（[../roadmap.md](../roadmap.md)）。
> **区別を残したまま、Gate では両方を弾く。**

#### 許可リストで書く。**禁止リストで書かない**

```python
# 両方漏れる（`Unknown` も enum の `unknown` も、この条件を満たす）
if world.get("user.activity_class") not in (MEETING, FOCUSED_WORK): ...

# 割り込んでよい値を列挙する。それ以外はすべて通さない
INTERRUPTIBLE = frozenset({IDLE, BROWSING})          # 〔Provisional〕3d の dry-run で見直す
if world.get("user.activity_class") not in INTERRUPTIBLE: return blocked
```

**禁止リストは、値が増えるたびに穴が開く。** `unknown` はその1例にすぎず、
**後から `presenting` や `on_call` を足した実装者が、Gate を直し忘れれば黙って通る。**
許可リストなら**新しい値は既定で通らない**——直し忘れは「話しかけなくなる」side に倒れる。

**`gaming` / `media` を許可に入れるかは決めていない**〔Provisional〕。
全画面はすでに別のゲートが弾いており、ウィンドウモードの扱いは
**3d の dry-run で「そのとき喋ろうとしたか」を見てから決める**（[../roadmap.md](../roadmap.md)）。
**決まるまでは通さない側に置く。**

#### TTL がこれを日常的に起こす

`user.activity_class` は導出 facet で、**入力のどれか1つが切れた瞬間に `Unknown` になる**
（[world-state.md](world-state.md) §3）。在席と全画面の観測が続いていても、
前面アプリの観測だけが止まれば分類は落ちる。**そこで割り込みを許すなら、TTL を短くした意味が無い。**

**規則: すべての facet ゲートは「取りうる値のうち、通してよいものを列挙する」形で書く。**
真偽値の facet は `is True` / `is False`（`==` や `not` で書かない）、
列挙の facet は上記の許可リストである。

> **Gate が「知らないから話しかけない」に倒れるのは正しい。** Phase 3 の完了条件は
> 「1日つけっぱなしにして不快でない」であり、**黙るのは失敗ではない。**

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
    max_tokens: int                 # 時間あたり LLM トークン（**prompt + completion**。下記）
    max_wallclock: timedelta        # 時間あたり自律活動の総時間

    interrupts_used: int
    tokens_used: int
    wallclock_used: timedelta
```

### ★ `tokens_used` は prompt + completion で数える〔2026-09-06〕

**数え方を決めておかないと、dry-run と本番で違う式を使ってしまう**——
そうなると 13b の上限は再現しない。

| | |
|---|---|
| **数える対象** | **prompt + completion の合計。** Ollama の `prompt_eval_count` + `eval_count` |
| なぜ completion だけにしないか | **自律のコストの大半は prompt 側にある。** [../measurements/phase2.md](../measurements/phase2.md) の実測で **prompt 529〜620 に対し生成 18〜24**——completion だけ数えると、**予算が実際の 4% しか見ていない**ことになる |
| 本番 | 応答のメタデータから実測を足す |
| dry-run | **同じ式に、決定論的な見積もりを入れる**（prompt はその時点で組み立てた実物を数えられる。completion だけが見積もり） |
| 推定であること | プロンプトのトークン数は**推定である**（[agent.md](agent.md) の「保証しないこと」）。**予算は上限であって課金ではない**ので、推定で足りる |

> **dry-run の prompt は実際に組み立てる。** 組み立ては LLM を呼ばないので抑止の対象ではなく、
> **組み立てないと予算の大半を占める側が見積もりになってしまう。**

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
| 4b | **facet が `Unknown` のとき、その条件は通らない**（`user.present` / `user.activity_class` / `desktop.fullscreen` を個別に期限切れにする） |
| 4c | **`user.activity_class` の入力を1つだけ期限切れにすると Gate が閉じる**（在席と全画面は生きたまま。導出 facet の TTL → [world-state.md](world-state.md) §3） |
| 4d | **分類が enum の `unknown` のとき Gate が閉じる**（**facet は新鮮なまま**。4b とは別の経路——知らないアプリが前面にある場合） |
| 4e | **`ActivityClass` の全値を流し、許可リストに無いものがすべて閉じる**（**値を足したら自動的に落ちる**。禁止リストに戻さないための検査） |
| 5 | Gate 不通過時に Drive が penalize される |
| 6 | AutonomyBudget が正しく消費・リセットされる |
| 7 | 「うるさい」で予算がゼロになり Drive が減衰する |
| 8 | 「うるさい」が Memory に記録される |
| 9 | 会話中の自律提案が `Deferred` になる |
| 10 | Quiet Hours 中に発火しない |
| 11 | `actor=self_initiated` で L2 が `ask`、L3 以上が `deny` になる（Phase 6） |
| 12 | **シミュレーション: 24時間分の World State 系列を流し、割り込み回数が予算内に収まる** |
| 13 | **dry-run と本番で、Gate の判定列と予算・cooldown・Drive の推移が一致する**（違うのは LLM 生成と発話の有無だけ。**12 の系列を両モードで流して比べる**） |
| 13b | **dry-run で `tokens_used` / `wallclock_used` も増える**（**`interrupts_used` だけ増える実装で落ちること**。トークン上限に先に当たる系列を流し、dry-run でも Gate が閉じる） |
| 13c | **dry-run で 2 回目以降の自律提案が通る**（**`complete()` を呼ばない実装で落ちること**。1回通した後、cooldown と予算を満たす系列を流し、`Deferred` ではなく `Accepted` になる） |
| 13d | **dry-run の tick 後、foreground が idle に戻っている**（13c の直接の検査） |
| 13e | **dry-run が発行した `ActivityStarted` / `ActivityEnded` に shadow の印が付く**（**印の無いイベントと区別して数えられること**） |

12 が「鬱陶しくないこと」の唯一の自動テスト。実際の体感は Phase 3 の完了条件で人間が判断する。
