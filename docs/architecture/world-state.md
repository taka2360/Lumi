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
| `user.present` | bool | 60s | sensor-desktop |
| `user.idle_seconds` | int | 30s | sensor-desktop |
| `user.focus_app` | str | 30s | sensor-desktop |
| `user.activity_class` | enum | 120s | sensor-desktop |
| `desktop.fullscreen` | bool | 30s | sensor-desktop |
| `audio.playing` | bool | 30s | sensor-desktop |
| `time.local` | datetime | 60s | core (built-in) |
| `time.quiet_hours` | bool | 300s | core (built-in) |
| `system.cpu` | float | 30s | sensor-desktop |
| `system.gpu_vram_free` | int | 30s | sensor-desktop |

`user.activity_class` の値〔Provisional〕: `idle` / `browsing` / `focused_work` / `meeting` / `gaming` / `media` / `unknown`

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

## 5. Sensor Extension

**out-of-process Capability Extension。** → [extension.md](extension.md)

| Sensor | 取得する facet |
|---|---|
| `sensor-desktop` | `user.*`, `desktop.*`, `audio.playing`, `system.*` |
| （将来）`sensor-calendar` | 予定、会議中か |
| （将来）`sensor-music` | 再生中の曲 |

### manifest での宣言

Sensor は「どの facet を書きたいか」を manifest で宣言する。宣言外の key を送っても Core が拒否する（Invariant 5）。

```jsonc
{
  "capabilities": {
    "sensors": [
      { "key": "user.present",     "ttl_ms": 60000 },
      { "key": "user.focus_app",   "ttl_ms": 30000 },
      { "key": "desktop.fullscreen", "ttl_ms": 30000 }
    ]
  }
}
```

### プライバシー

`user.focus_app` はアプリ名を取る。**ウィンドウタイトルは取らない**（機密情報が入りうる）。

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
| `sensor-desktop` Extension | Windows の foreground app / idle / fullscreen |
| Inspector 表示 | facet 一覧（期限切れは灰色）、Internal State |

---

## 7. テスト

| # | テスト |
|---|---|
| 1 | TTL を過ぎた facet が `Unknown` を返す |
| 2 | Sensor が宣言外の key を送ると拒否される |
| 3 | Sensor Signal が WorldFacet を直接書かない（Core 経由） |
| 4 | WorldSnapshot が一貫している（取得中に facet が変わっても） |
| 5 | projection が期限切れ facet を「分からない」と表現する |
| 6 | Mood の慣性が1回の delta で急変させない |
| 7 | Mood が時間経過でニュートラルへ減衰する |
| 8 | Internal State が Extension / Stage から書けない |
| 9 | 表情が Mood + ACT の合成になる |
| 10 | projection のスナップショットテスト（入力 facet 集合 → 出力文字列） |
