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
    source: SensorId

    def is_valid(self, now: datetime) -> bool:
        return now - self.observed_at < self.ttl
```

**期限切れた facet は「知らない」を意味する。** `None` を返すのではなく、`Unknown` として扱い、プロンプトにも「分からない」と投影する。

### Facet 一覧〔Provisional〕

| key | 型 | TTL 目安 | Sensor |
|---|---|---|---|
| `user.present` | bool | 60s | Desktop Sensor |
| `user.idle_seconds` | int | 30s | Desktop Sensor |
| `user.focus_app` | str | 30s | Desktop Sensor |
| **`user.activity_class`** | enum | 120s | **Core**（下記。Sensor は送らない） |
| `desktop.fullscreen` | bool | 30s | Desktop Sensor |
| `audio.playing` | bool | 30s | Desktop Sensor |
| `system.cpu` | float | 30s | Desktop Sensor |
| `system.gpu_vram_free` | int | 30s | Desktop Sensor |

`user.activity_class` の値〔Provisional〕: `idle` / `browsing` / `focused_work` / `meeting` / `gaming` / `media` / `unknown`

### ★ `user.activity_class` は観測ではなく分類である〔2026-09-06〕

**Sensor はこれを送らない。** `meeting` / `focused_work` は
**`AutonomyGate` が「割り込んでよいか」を直接これで決める**値であり（[autonomy.md](autonomy.md) §4）、
**Sensor が送れるなら Sensor が割り込みの可否を決めていることになる**（Invariant 1）。

**Core が `sensor.*` Signal ハンドラの中で、生の観測（前面アプリ / idle / 全画面 / 音声再生）から
決定論的に導出する。** ハンドラの中で書くのは、静的検査 #10
（`WorldFacet` の書き込みは Signal ハンドラ以外に存在しない →
[../contracts/authority-matrix.md](../contracts/authority-matrix.md)）を満たすためでもある。

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

**Core は OS をポーリングしない。Sensor Extension が Signal を push する。**

```
Sensor Ext
  → Signal(type="sensor.foreground_app", payload={"app": "factorio.exe"})
  → Core: 認証 / schema 検証 / capability 検査
  → Core: WorldFacet("user.focus_app") を更新
  → Core: DomainEvent(stream_key="world:user.focus_app", type="WorldFacetChanged")
```

**Sensor は facet を直接書かない。** Core が書く（[../contracts/authority-matrix.md](../contracts/authority-matrix.md)）。

理由: Sensor が任意の key に任意の値を書けると、Core が認識していない状態が生まれる（Invariant 6 違反）。Core が key の妥当性・型・TTL を決める。

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
| 時刻 | システムから取れなくなれば分からない | World |

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
| **Desktop Sensor** | `user.*`, `desktop.*`, `audio.playing`, `system.*` | **Shell（Rust）**〔Phase 3〕 |
| Clock | `time.*` | Core built-in |
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
| 検査 | Core が**送出元ごとに、送ってよい key の集合**と照合して拒否する（Invariant 5） |
| 権限判断 | Sensor は持たない（Invariant 1） |
| **trust** | **`sensor.*` の payload は `UNTRUSTED`。** 送出元が Shell でも変わらない（[../contracts/provenance.md](../contracts/provenance.md)）——`user.focus_app` は**アプリが自分で名乗った文字列**である |
| TTL / confidence | **権威は Core が持つ**（§3 の表）。manifest の `ttl_ms` は**上限のヒント**で、**Core は自分の値と短い方を採る**——Extension が観測を Core の意図より長生きさせられない |
| 分類 | **Sensor は送らない。** `user.activity_class` は Core が導出する（§3） |

**「宣言外の key を送っても Core が拒否する」は形態に依存しない。** 宣言の置き場所だけが変わる——
Extension は manifest、Shell は Shell のコード（**実行時に増えない固定集合**）である。

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
| 初回 | **何を観測するかを開示する**（前面アプリ名 / idle / 在席 / 全画面 / 音声再生 / CPU・VRAM） |
| 設定 | **Desktop Sensor を無効にできる。無効ならスレッドを起動しない**（「送らない」ではなく「観測しない」） |
| 永続化 | `world:*` の DomainEvent は既定 30 日（[../contracts/privacy.md](../contracts/privacy.md) §2 の行 5）。**新しい保存先は作らない** |

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
| 2 | Sensor が宣言外の key（Shell なら固定集合の外）を送ると拒否される |
| 3 | Sensor Signal が WorldFacet を直接書かない（Core 経由） |
| 4 | WorldSnapshot が一貫している（取得中に facet が変わっても） |
| 5 | projection が期限切れ facet を「分からない」と表現する |
| 6 | Mood の慣性が1回の delta で急変させない |
| 7 | Mood が時間経過でニュートラルへ減衰する |
| 8 | Internal State が Extension / Stage から書けない |
| 9 | 表情が Mood + ACT の合成になる |
| 10 | projection のスナップショットテスト（入力 facet 集合 → 出力文字列） |
