# World State と Internal State

> **外界についてLumiが知っていること**と、**Lumi自身がどうであるか**は、性質が根本的に異なる。分離する。

親: [DESIGN.md](../DESIGN.md) / 関連: [memory.md](memory.md), [autonomy.md](autonomy.md), [ADR-014](../decisions/ADR-014-world-vs-internal-state.md)

---

## 1. なぜ分けるのか

| | **World State** | **Internal State** |
|---|---|---|
| 由来 | Sensor による**観測** | Lumi 自身の**経験の蓄積** |
| 失効 | **TTL で失効する**（「知らない」に戻る） | 失効しない。減衰・遷移する |
| confidence | **ある**（観測の確からしさ） | ない（自分の状態に確信は不要） |
| 書き手 | Core（Sensor Signal を受けて） | Core 内部のみ |
| 例 | `user.present`, `desktop.fullscreen` | `mood`, `fatigue`, `drives` |

「ユーザーが在席しているか」は観測であり、観測が古くなれば「分からない」に戻るべき。
「Lumi の機嫌」は観測ではなく、古くなっても「分からない」にはならない。

### 三分離の原則

```
World State    外界の観測      安い / derived / 失効する
Internal State 自分の状態      安い / 蓄積される / 失効しない
Memory         覚えていること   高い / curated / 減衰する
```

**混同すると:**
- World State を Memory に入れる → ゴミ記憶が量産される（「10:31 に Chrome が前面だった」を覚える意味はない）
- Memory を状態管理に使う → 検索コストが状態参照のたびにかかる
- Internal State を World facet にする → mood が TTL で失効して「機嫌が分からない」になる

---

## 2. World State

### Facet

```python
@dataclass(frozen=True)
class WorldFacet:
    key: str
    value: Any
    confidence: float           # 0.0-1.0
    observed_at: datetime
    ttl: timedelta
    source: FacetSource         # SensorId | "core.derived"（下記）
    # provenance は2つで1組。**片方だけ持つと propagate() に渡せない**（下記）
    provenance_class: ProvenanceClass   # 監査とユーザーへの説明のラベル
    trust_level: TrustLevel             # Policy が読む値。**再計算しない**（Invariant 7）

    def is_valid(self, now: datetime) -> bool:
        return now - self.observed_at < self.ttl
```

**期限切れた facet は「知らない」を意味する。** `None` を返すのではなく、`Unknown` として扱い、プロンプトにも「分からない」と投影する。

### ★ `source` は Sensor とは限らない〔2026-09-06〕

**`user.activity_class` は Core が導出する**（§3）ので、**どの Sensor も出どころではない。**
`source: SensorId` のままだと、**Core の判断を Desktop Sensor のせいにするしかない**——
Inspector で「Sensor がそう言った」と表示され、**分類器のバグを Sensor の不具合として追うことになる。**

```python
FacetSource = SensorId | Literal["core.derived"]
```

| 値 | 意味 |
|---|---|
| `SensorId` | **観測。** その Sensor が送った Signal がそのまま facet になった |
| `"core.derived"` | **導出。** Core が他の facet から計算した（`user.activity_class`） |

**`provenance_class` とは別の軸である。** `provenance_class` は「**信用してよいか**」、
`source` は「**誰が言ったか**」を答える。
導出 facet は `source = "core.derived"` かつ `provenance_class = DERIVED` になるが、
**Extension が送った生の観測**も `DERIVED` にはならない（`UNTRUSTED`）ので、一対一ではない。

### ★ `trust_level` を facet が持つ理由〔2026-09-06 / [ADR-050](../decisions/ADR-050-desktop-sensor-in-shell.md)〕

**facet に置き場所が無いと、汚染は保存の時点で消える。**
`Signal` は `trust_level` を持つが（[../contracts/event-model.md](../contracts/event-model.md)）、
facet がそれを落とすと、**projection がどれを隔離ブロックに入れるべきか判断できない。**

| | |
|---|---|
| 誰が決めるか | **Core**（Signal ハンドラ）。**`sensor.*` は送出元によらず `ProvenanceClass.UNTRUSTED` / `TrustLevel.TAINTED`**（下記） |
| **なぜ2つ持つか** | `propagate()` は `Provenanced`（**両方を持つもの**）を受け取る。**`trust_level` だけの facet は導出の入力にできない** |
| 導出した facet | `user.activity_class` は元にした観測から `propagate()` する → **`DERIVED`**（生の観測の `UNTRUSTED` とは区別される）。`trust_level` は `taint()` で決まり、**どちらも `TAINTED`**（Invariant 7） |
| projection | **tainted な facet は隔離ブロックに入れる**（[../contracts/provenance.md](../contracts/provenance.md)） |

**`UNTRUSTED` と `DERIVED` の区別を facet で潰さない。** Policy はどちらも `TAINTED` として
同じに扱うが、**監査とユーザーへの説明では別物である**——
「アプリがそう名乗った」と「Lumi がそこから分類した」を、Inspector で同じ顔にしない。
`ProvenanceClass` を落とすと、**この区別は facet になった瞬間に永久に失われる。**

### Facet 一覧〔Provisional〕

| key | 型 | TTL 目安 | Sensor |
|---|---|---|---|
| `user.present` | bool | 60s | Desktop Sensor |
| `user.idle_seconds` | int | 30s | Desktop Sensor |
| `user.focus_app` | str | 30s | Desktop Sensor |
| **`user.activity_class`** | enum | **入力の残り TTL の最小**（下記） | **Core**（下記。Sensor は送らない） |
| `desktop.fullscreen` | bool | 30s | Desktop Sensor |
| `audio.playing` | bool | 30s | Desktop Sensor |
| `system.cpu` | float | 30s | Desktop Sensor |
| `system.gpu_vram_free` | int | 30s | Desktop Sensor |

`user.activity_class` の値〔Provisional〕: `idle` / `browsing` / `focused_work` / `meeting` / `gaming` / `media` / `unknown`

> **★ `unknown`（enum 値）と `Unknown`（sentinel）は別物である。**
>
> | | 意味 | どこの層 |
> |---|---|---|
> | **`Unknown`** | **facet が無い。** 観測が届いていないか期限切れ（Sensor が黙った） | facet 層（§2。すべての facet に起こる） |
> | **`unknown`** | **観測はある。** 前面アプリは分かるが、**それが何の活動か分類できない** | `user.activity_class` の値 |
>
> **消して1つにしない。** 「Sensor が生きているか」と「このアプリが何か分かるか」は違う質問で、
> **3d の dry-run で「なぜ発火しなかったか」を読むときに区別が要る。**
>
> **ただし `AutonomyGate` は両方とも通さない**——片方だけ弾く実装は必ずもう片方で漏れる。
> Gate は**割り込んでよい値の許可リスト**で書く（[autonomy.md](autonomy.md) §4）。

### ★ `user.activity_class` は観測ではなく分類である〔2026-09-06〕

**Sensor はこれを送らない。** `meeting` / `focused_work` は
**`AutonomyGate` が「割り込んでよいか」を直接これで決める**値であり（[autonomy.md](autonomy.md) §4）、
**Sensor が送れるなら Sensor が割り込みの可否を決めていることになる**（Invariant 1）。

**Core が `sensor.*` Signal ハンドラの中で、生の観測（前面アプリ / idle / 全画面 / 音声再生）から
決定論的に導出する。** ハンドラの中で書くのは、静的検査 #10
（`WorldFacet` の書き込みは Signal ハンドラ以外に存在しない →
[../contracts/authority-matrix.md](../contracts/authority-matrix.md)）を満たすためでもある。

#### ★ 導出 facet の TTL は入力より長くできない

**導出値に独立した TTL を持たせると、根拠が全部切れた後も分類だけが生き残る。**

`user.focus_app` は 30 s、`user.present` は 60 s である。
`activity_class` に固定 120 s を与えると、**前面アプリの観測が止まってから 90 s のあいだ、
Core が「今どのアプリを見ているか知らない」まま `browsing` を主張し続ける。**
そして `AutonomyGate` はそれを見て割り込みを許す——**TTL が fail-closed に倒すはずだった、まさにその点で。**

```text
ttl(derived) = min(残り TTL of その値の導出に実際に使った facet)
```

| | |
|---|---|
| 規則 | **導出 facet は、根拠のどれか1つが切れた瞬間に `Unknown` になる** |
| 数え方 | **実際に使った入力だけ**。`gaming` の判定に音声再生を見ていないなら、その TTL は効かない |
| 実装 | 読み出し時に導出してもよい（そのほうが安全側）。**保存するなら上式の TTL を必ず付ける** |

**`Unknown` は Gate を通さない。** 分からないときに割り込まないのが、この Phase の設計方針である。
**ただしそれは Gate 側が「既知であること」を条件に書いて初めて成立する**——
「`meeting` でない」は `Unknown` でも真になる。→ [autonomy.md](autonomy.md) §4

### ★ `time.*` は facet ではなく導出値である〔2026-09-06〕

**時計を facet にすると、TTL も Signal も持てないものに TTL と Signal を要求することになる。**

| | |
|---|---|
| 陳腐化しない | `now()` は常に最新で、`Unknown` になる瞬間が無い。**TTL に意味が無い** |
| Sensor がいない | 外部から届く通知ではないので `Signal` にできない（`Signal` は「外部から Core に届く通知」） |
| 書き手がいない | Core が直接 facet を書くと**静的検査 #10 を落とす**。例外を1つ作るより、facet をやめるほうが安い |

**`time.local` / `time.quiet_hours` は、必要な場所が時計と設定から直接計算する。**
「最終対話からの経過時間」（§4）と同じ**導出値**の扱いである。

### 書き込み経路

**Core は OS をポーリングしない。Sensor が Signal を push する。**
Phase 3 の Sensor は **Shell**（[ADR-050](../decisions/ADR-050-desktop-sensor-in-shell.md)）、
将来の外部 Sensor は Extension である。**経路は同じで、送出元だけが違う**（§5）。

```
Shell（Desktop Sensor）〔Phase 3〕 / Capability Extension〔Phase 9〜〕
  → Signal(type="sensor.foreground_app", payload={"app": "factorio.exe"})
  → Core: 認証 / schema 検証 / **送出元ごとの許可 key 集合**と照合（B8）
  → Core: trust を決める（`sensor.*` は tainted 固定）
  → Core: WorldFacet("user.focus_app") を更新
  → Core: DomainEvent(stream_key="world:user.focus_app", type="WorldFacetChanged")
```

**Sensor は facet を直接書かない。** Core が書く（[../contracts/authority-matrix.md](../contracts/authority-matrix.md)）。

理由: Sensor が任意の key に任意の値を書けると、Core が認識していない状態が生まれる（Invariant 6 違反）。Core が key の妥当性・型・TTL を決める。

#### ★ 送るのは変化だけではない — TTL より短い周期で送り続ける

**facet は届かなければ期限切れる。** `is_valid()` は `now - observed_at < ttl` なので、
**「変わったときだけ送る」実装は、変わらない限り必ず `Unknown` に落ちる。**
`user.focus_app` の TTL は 30 s であり、**30 s ごとに送ると間に合わない**——
スケジューリングの揺らぎで `observed_at + 30s` を跨いだ瞬間に切れる。

そして `user.activity_class` はその導出なので**一緒に `Unknown` になり、`AutonomyGate` が閉じる**。
**Sensor の周期の選び方ひとつで、自律発話が静かに止まる。**

| | |
|---|---|
| 規則 | **送出周期 ≤ TTL / 2。** 変化が無くても送る（ハートビート） |
| いま | `user.focus_app` / `desktop.fullscreen` / `audio.playing` / `system.*` は TTL 30 s → **周期 10 s**。`user.present` は 60 s → **周期 20 s** |
| **変化時** | 周期を待たずに**即座に送る**（前面アプリが変わったことは早く知りたい） |
| 逆向きの制約 | **TTL は「観測が止まったことに気づくまでの時間」でもある。** 伸ばせば解決するが、**止まった Sensor を長く信じることになる**——だから周期を短くする側で解く |

> **これは Core 側の設定ではなく Sensor 側の責務である。** Core は TTL しか知らず、
> **Sensor が黙ったのか、値が変わらないのかを区別できない**（区別する必要も無い）。

### プロンプトへの投影

**全状態ではなく、圧縮した projection のみ入れる。**

```python
def project(snapshot: WorldSnapshot) -> str:
    """人間が読める短い記述に落とす。生の facet 列を並べない。"""
    # 例: 「センパイは在席中。30分ほど Factorio を触っている。今は21時。」
```

理由:
- 生の facet 列（`user.idle_seconds=42`）は LLM が使いにくく、トークンも食う
- **投影のロジックが Core にあることで、何を LLM に見せるかを制御できる**

---

## 3. Internal State

```python
@dataclass
class InternalState:
    mood: Mood                    # 持続的な気分。慣性と減衰を持つ
    fatigue: float                # 疲労。連続稼働・高負荷で上昇
    arousal: float                # 覚醒度
    rest_pressure: float          # 休息圧。深夜・長時間で上昇
    attention_focus: str | None   # 今の関心の対象
    current_goal: Goal | None     # 進行中の目標
    drives: dict[Drive, float]    # 各 Drive の現在値
```

### Mood — 持続する状態

**AIRI では感情が LLM 出力から毎ターン導出される瞬間値でしかなく、これが「生きている感じ」を最も損なっている。**

Lumi の Mood は:

| 性質 | 内容 |
|---|---|
| **慣性** | 急には変わらない。1回の会話で機嫌が180度変わらない |
| **減衰** | 放っておくとニュートラルに戻る |
| **影響先** | プロンプト（口調）/ Drive（乗数）/ 表情（ベースライン） |

```python
def update_mood(self, delta: MoodDelta, dt: timedelta):
    # 慣性: 変化量を制限
    applied = clamp(delta, -MAX_STEP, MAX_STEP)
    self.mood = blend(self.mood, self.mood + applied, INERTIA)
    # 減衰: ニュートラルへ
    self.mood = decay_toward_neutral(self.mood, dt, TAU_MOOD)
```

### 表情との関係

**最終的な表情 = Mood（ベースライン） + `<|ACT|>` マーカー（瞬間値）の合成。**
合成規則の定義 → [ui.md](ui.md) §3

Internal State 側の責務は「Mood をベースラインとして提供すること」だけ。**合成は Core（Character 層）が行い、Renderer は関与しない。**

### 書き込み経路

**Core 内部のみ。** Extension も Stage も Internal State を直接書けない。

Signal（「うるさい」など）は受け取るが、それを Mood にどう反映するかは Core が決める。

---

## 4. 何を World に置き、何を Internal に置くか

判定: **「観測できなくなったら『分からない』になるか?」**

| | 分からなくなる | World |
|---|---|---|
| ユーザーが在席か | Sensor が止まれば分からない | World |
| 前面アプリ | 同上 | World |
| Lumi の機嫌 | Sensor が止まっても Lumi は自分の機嫌を知っている | **Internal** |
| Lumi の疲労 | 同上 | **Internal** |
| 現在の目標 | 同上 | **Internal** |
| Drive 値 | 同上 | **Internal** |
| 時刻 | **どちらでもない**（導出値）。時計は陳腐化せず、Sensor も TTL も持たない → §3 | — |

### 迷いやすい例

| 項目 | 分類 | 理由 |
|---|---|---|
| 最終対話からの経過時間 | **どちらでもない**（導出値） | `time.local` と Memory の最新 episode から計算する |
| ユーザーの好み | **Memory** | 観測ではなく、蓄積された信念 |
| 今話している話題 | **Working Memory** | セッション内。Internal でも World でもない |
| 未読の通知数 | World | 観測。Sensor が取る |

---

## 5. Sensor

**Sensor は「OS や外部を観測して Signal を送るもの」であり、実装形態は1つではない**
〔2026-09-06 決着 → [ADR-050](../decisions/ADR-050-desktop-sensor-in-shell.md)〕。

| Sensor | 取得する facet | 実装形態 |
|---|---|---|
| **Desktop Sensor** | `user.*`（`activity_class` を除く）, `desktop.*`, `audio.playing`, `system.*` | **Shell（Rust）**〔Phase 3〕 |
| （将来）`sensor-calendar` | 予定、会議中か | out-of-process Capability Extension〔Phase 9〕 |
| （将来）`sensor-music` | 再生中の曲 | 同上 |

**Phase 3 の Desktop Sensor を out-of-process Extension にしない理由**は
[ADR-050](../decisions/ADR-050-desktop-sensor-in-shell.md) にある。要点は
**Extension ホストを Phase 4b（Browser / Class B）でどのみち作ること**と、
**out-of-process にしても OS に対する境界にはならないこと**の2つである。

### 形態が変わっても変わらないこと

| | |
|---|---|
| 送るもの | **`Signal` だけ。** WorldFacet を更新するのは Core（Invariant 6） |
| 検査 | Core が**自分の側にある「送出元ごとの許可 key 集合」**と照合して拒否する（Invariant 5。下記） |
| 権限判断 | Sensor は持たない（Invariant 1） |
| **trust** | **`sensor.*` は `ProvenanceClass.UNTRUSTED` / `TrustLevel.TAINTED`。** 送出元が Shell でも変わらない（[../contracts/provenance.md](../contracts/provenance.md)）——`user.focus_app` は**アプリが自分で名乗った文字列**である。**`WorldFacet` が `provenance_class` と `trust_level` を持ち、projection まで運ぶ**（§2） |
| TTL / confidence | **権威は Core が持つ**（§3 の表）。manifest の `ttl_ms` は**上限のヒント**で、**Core は自分の値と短い方を採る**——Extension が観測を Core の意図より長生きさせられない |
| 分類 | **Sensor は送らない。** `user.activity_class` は Core が導出する（§3） |

### ★ 許可 key の集合は Core が持つ〔2026-09-06〕

**送出元が自分で持っているリストは、capability 境界ではない。**
この向き（Shell → Core）は **[../contracts/security-boundaries.md](../contracts/security-boundaries.md) の B8** である。
Shell が壊れていても、バージョンがずれていても、乗っ取られていても、
**Core は「この送出元はこの key を送ってよいか」を自分の側の表だけで答えられなければならない**（Invariant 5）。

| 送出元 | 宣言はどこに書くか | **判定に使うのはどれか** |
|---|---|---|
| Shell（Desktop Sensor） | Core が持つ**送出元 `shell` の許可 key 集合**。[wire.json](../contracts/wire.json) の schema にも写す | **Core の集合** |
| out-of-process Extension | Extension の manifest。**同意時に Core が永続化する** | **Core が永続化した集合** |

**Shell のコードにある「送る key の一覧」は実装の都合であって宣言ではない。**
これを宣言として扱うと、**送出元が自分の権限を決めていることになる。**

> **schema 検証だけでは足りない。** `sensor.*` として妥当な key であることと、
> **その送出元が送ってよい key であること**は別である。前者だけだと、Shell が
> `calendar.in_meeting` や `user.activity_class` を名乗れてしまい、
> **それは AutonomyGate の判断に直接効く**（[autonomy.md](autonomy.md) §4）。

### manifest での宣言〔out-of-process Sensor の場合〕

```jsonc
{
  "id": "example.sensor-calendar",
  "capabilities": {
    "sensors": [
      { "key": "calendar.in_meeting",   "ttl_ms": 60000 },
      { "key": "calendar.next_event_in", "ttl_ms": 60000 }
    ]
  }
}
```

> **この例はまだ存在しない Sensor である。** 実装済みのもの（Desktop Sensor）を
> manifest の例に使うと、**動いていない機構が動いているように読める。**

### プライバシーと同意

`user.focus_app` はアプリ名を取る。**ウィンドウタイトルは取らない**（機密情報が入りうる）。

**この規則は Shell 側の制約としてそのまま持つ。** 実装形態が変わっても緩めない。

**★ 同意の門を落とさない**〔2026-09-06 / [ADR-050](../decisions/ADR-050-desktop-sensor-in-shell.md)〕。
Extension だったときは [extension.md](extension.md) §6 の `consent` を通らないと `ready` にならなかった。
Shell に移したことで、その門が黙って消えてはならない。

| | |
|---|---|
| 初回 | **何を観測するかを提示し、明示的な許可を得る**（前面アプリ名 / idle / 在席 / 全画面 / 音声再生 / CPU・VRAM） |
| **既定** | **許可されるまで起動しない**（opt-in）。**開示だけして既定オンにはしない** |
| 永続化 | 許可は設定に保存する。**毎回聞かない。観測する key が増えたら再同意** |
| 断ったとき | Sensor のスレッドを起動しない（「送らない」ではなく**観測しない**）。facet は `Unknown` のままで、`AutonomyGate` は通さない側に倒れる |
| 保存先 | `world:*` の DomainEvent は既定 30 日（[../contracts/privacy.md](../contracts/privacy.md) §2 の行 5）。**新しい保存先は作らない** |

タイトルが必要な機能を作る場合は、別の capability として明示的に宣言させ、ユーザー同意を必須にする。

---

## 6. Phase 3 で作るもの

| 項目 | 内容 |
|---|---|
| WorldFacet の型と TTL 管理 | 期限切れの扱い、`Unknown` の表現 |
| Sensor Signal の受信と検証 | capability 検査、key の妥当性 |
| WorldSnapshot | ある時点の一貫したスナップショット（`structuredClone` 相当） |
| projection | プロンプト用の圧縮記述 |
| InternalState | mood / fatigue / arousal / rest_pressure / drives |
| Mood の慣性と減衰 | |
| **Desktop Sensor**（Shell） | Windows の foreground app / idle / fullscreen / 在席 → Signal で Core へ（[ADR-050](../decisions/ADR-050-desktop-sensor-in-shell.md)） |
| Inspector 表示 | facet 一覧（期限切れは灰色）、Internal State |

---

## 7. テスト

| # | テスト |
|---|---|
| 1 | TTL を過ぎた facet が `Unknown` を返す |
| 2 | Sensor が**その送出元に許可されていない key** を送ると拒否される（**判定は Core 側の集合。送出元の申告を見ない**） |
| 2b | Shell が `user.activity_class` や他 Sensor の key を名乗っても拒否される（**schema 上は妥当でも通さない**） |
| 3 | Sensor Signal が WorldFacet を直接書かない（Core 経由） |
| 4 | WorldSnapshot が一貫している（取得中に facet が変わっても） |
| 5 | projection が期限切れ facet を「分からない」と表現する |
| 6 | Mood の慣性が1回の delta で急変させない |
| 7 | Mood が時間経過でニュートラルへ減衰する |
| 8 | Internal State が Extension / Stage から書けない |
| 9 | 表情が Mood + ACT の合成になる |
| 10 | projection のスナップショットテスト（入力 facet 集合 → 出力文字列） |
| 11 | `sensor.*` の Signal が `ProvenanceClass.UNTRUSTED` / `TrustLevel.TAINTED` になる（**送出元が Shell でも**） |
| 12 | facet の provenance が projection まで運ばれ、**tainted な facet が隔離ブロックに入る** |
| 12b | **導出 facet が `DERIVED` になる**（生の観測の `UNTRUSTED` と区別され、`trust_level` はどちらも `TAINTED`） |
| 12c | **導出 facet の `source` が `core.derived` になる**（**どの `SensorId` でもない**。Core の判断を Sensor に帰属させない） |
| 13 | **導出 facet の TTL が入力の残りの最小を超えない**（`focus_app` を 30 s 止めたら `activity_class` も `Unknown` になる） |
| 14 | `time.*` が facet として存在しない（**Core が直接書く経路が無い**。静的検査 #10） |
| 15 | **許可されるまで Desktop Sensor が起動しない**。許可は永続化され、次回は聞かれない |
| 16 | 許可を断った状態で `AutonomyGate` が通らない（`Unknown` は fail-closed） |
| 16b | **`Unknown`（facet 無し）と `unknown`（分類失敗）が区別して Inspector に出る**（Gate はどちらも閉じるが、**理由は違う**） |
| 17 | **値が変わらなくても facet が期限切れない**（Sensor を回したまま TTL の 3 倍待ち、`is_valid()` が真であり続ける） |
| 18 | **Sensor が黙ったら facet が `Unknown` になる**（17 の裏。**止まったことに気づけること**） |
| 19 | **`sensor.*` の payload の形が Shell と Core で一致する**（`wire.json` は形を検査しないので、**両言語に別途置く** → [../contracts/wire.md](../contracts/wire.md) §4） |
