# Interface: CharacterRenderer

> **`set_expression` は「意図」を受け取る。パラメータではない。**

親: [DESIGN.md](../DESIGN.md) / 関連: [../architecture/ui.md](../architecture/ui.md) / [ADR-009](../decisions/ADR-009-renderer-intent-based.md)

---

## なぜ「意図」なのか

VRM と Live2D は表情モデルが**根本的に異なる**。

| | VRM | Live2D |
|---|---|---|
| モデル | 名前付きブレンドシェイプの合成（`happy` / `aa` / `blink`） | 生パラメータの直接操作（`ParamEyeLOpen` / `ParamMouthForm`） |
| 抽象度 | 高い（`happy` が定義されている） | 低い（`happy` は複数パラメータの組み合わせ） |
| API | `expressionManager.setValue("happy", 0.7)` | `model.setParameterValueById("ParamBrowLY", -0.3)` |

`CharacterRenderer` がパラメータを露出すると、**Live2D 追加時（Phase 9）に必ず破綻する**。Core が VRM のブレンドシェイプ名を知っていたら、Live2D では意味をなさない。

AIRI はこの2つを別々のパッケージ（`stage-ui-three` / `stage-ui-live2d`）として並列に実装しており、**共通の抽象基底を持っていない**。`Emotion` enum が4箇所に重複定義され、マッピングテーブルが微妙に異なる（`stage-ui-live2d` の VRM 表情マッピングに `undefined` が混ざっている）という状態になっている。

Lumi は最初から抽象を1つ持つ。

---

## Interface

```python
class CharacterRenderer(Protocol):
    def apply_expression(self, intent: ExpressionIntent) -> None: ...
    def apply_motion(self, intent: MotionIntent) -> None: ...
    def apply_viseme(self, frame: VisemeFrame) -> None: ...
    def look_at(self, target: GazeTarget) -> None: ...

    def capabilities(self) -> RendererCapabilities: ...
    def hit_region(self) -> HitRegion:
        """クリックスルーのヒットテスト用。Shell に渡される。"""
```

---

## ExpressionIntent

```python
@dataclass(frozen=True)
class ExpressionIntent:
    emotion: Emotion
    intensity: float        # 0.0-1.0
    blend_ms: int           # 遷移時間
    duration_ms: int | None # None なら維持。指定があれば自動で戻る


class Emotion(Enum):
    NEUTRAL   = "neutral"
    HAPPY     = "happy"
    SAD       = "sad"
    ANGRY     = "angry"
    SURPRISED = "surprised"
    THINK     = "think"
    CURIOUS   = "curious"
    AWKWARD   = "awkward"
    SLEEPY    = "sleepy"
```

**この enum は1箇所にだけ定義する。** Renderer ごとに再定義しない。

### フォールバック

**Renderer が表現できない意図は、Renderer 側で最も近い意図にフォールバックする。Core は知らない。**

```python
# VRM Renderer 内
FALLBACK = {
    Emotion.CURIOUS: Emotion.THINK,
    Emotion.AWKWARD: Emotion.SAD,
    Emotion.SLEEPY:  Emotion.NEUTRAL,
}
```

Core が `capabilities()` を見て分岐する設計にはしない。分岐が Core に入ると、Renderer が増えるたびに Core が変わる。

### `intensity` の上限

VRM のブレンドシェイプは 1.0 まで指定できるが、**実際には 0.7-0.8 程度に抑えたほうが自然に見える**（AIRI も同じ結論に達しており、値を抑える対処をしている）。

これは Renderer 側の実装詳細として扱う。Core は 0.0-1.0 で意図を伝え、Renderer が実際の値域にマップする。

---

## MotionIntent

```python
@dataclass(frozen=True)
class MotionIntent:
    name: str               # Content Pack で定義されたモーション名
    loop: bool
    blend_ms: int
    priority: int           # アイドルモーションより高いか
```

モーション名は Content Pack が定義する（`content/characters/<id>/motions/`）。Core はハードコードしない。

---

## VisemeFrame（リップシンク）

```python
@dataclass(frozen=True)
class VisemeFrame:
    weights: Mapping[Viseme, float]   # A / E / I / O / U
    mouth_open: float                  # 0.0-1.0


class Viseme(Enum):
    A = "A"; E = "E"; I = "I"; O = "O"; U = "U"
```

VRM の標準ビセーム（`aa` / `ee` / `ih` / `oh` / `ou`）にマップする。

### 生成方式 — **口の形は音素列から、時間は音声から**〔Confirmed。2026-08-15 / Phase 0 実測〕

| 方式 | 評価 | 採否 |
|---|---|---|
| **TTS の音素列（モーラ）から口の形を決める** | 母音を取り違えない。エンジンが返す情報そのもの | **採用** |
| 再生中の音声の振幅から推定 | エンジン非依存。AIRI は wLipSync を使用 | 不採用（母音が分からない） |

`audio_query` の `accent_phrases[].moras[]` に `vowel`（`a`/`i`/`u`/`e`/`o`/`N`/`cl`/`pau`）が入る。
**これが口の形の唯一の入力。**

#### ★ 音素の長さは、返ってこないことがある〔2026-08-15 実測〕

**AivisSpeech は `audio_query` のモーラ長を すべて `0.0` で返す。**
`engine_manifest` の `adjust_phoneme_length: false` がその宣言であり、
長さは合成時にモデルが決めるため、事前には存在しない。VOICEVOX は返す。

| エンジンが返すもの | 時間の決め方 |
|---|---|
| 音素長がある（VOICEVOX） | **その値を使う。** 実際の発話と一致する |
| 音素長が無い（AivisSpeech） | **合成された音声の長さをモーラ列で割り振る**（閉じるモーラは短め） |
| どちらも無い | **ビセームを送らない。** 口は閉じたまま（でたらめな時間で動かさない） |

**設計上の帰結: タイムラインは合成の「あと」にしか作れない。**
当初は「合成前に得られる」と書いていたが、実測で誤りだった。
`stage.speech.started` は**再生開始と同時**に送るので、体感上の遅れは無い。

割り振りは近似であり、**モーラごとの長短は実際の発話と一致しない。**
一致させたければ、音声から音素境界を推定する処理が要る（Phase 0 では作らない）。

```python
@dataclass(frozen=True)
class VisemeSpan:
    """1つのビセームを、いつからいつまで出すか。**Core が作る。**"""
    viseme: Viseme | None   # None = 口を閉じる（無音・撥音・促音）
    start_ms: int           # 発話開始からの相対時刻
    duration_ms: int


@dataclass(frozen=True)
class VisemeTimeline:
    spans: list[VisemeSpan]
    total_ms: int
```

### Core → Renderer の契約

| method | 向き | 内容 |
|---|---|---|
| `stage.speech.started` | Core → Stage | `{text, timeline, total_ms}`。**再生開始と同時に送る** |
| `stage.speech.ended` | Core → Stage | `{}`。再生が終わった / 中断された |

**1発話の中で `started` が複数回来る**〔Phase 1 実装時に確定〕。TTS は文単位に生成・再生され
（[../architecture/audio.md](../architecture/audio.md) §6）、タイムラインは文ごとにしか作れない。
**`started` を受けたら、Stage は前のタイムラインを捨てて新しい方に切り替える。**
`ended` は発話全体が終わった / 中断されたときに **1回だけ**来る。

**時刻は Stage 側の時計で進める。** 1フレームごとに Core から送ると 60Hz の WS 往復になり、
Stage が詰まると口が固まる（ホバー検知と同じ理由 → [../architecture/ui.md](../architecture/ui.md)）。

**`ended` が来なかった場合、Stage は `total_ms` を過ぎた時点で口を閉じる。**
Core が落ちても口が開きっぱなしにならない（fail-closed）。

**アタック/リリースの非対称スムージングと無音判定は必須。** これが無いと口がガクガクする。
**スムージングは Renderer 側で行う**（表現の詳細であり、Core は意図だけを送る）。

---

## RendererCapabilities

```python
@dataclass(frozen=True)
class RendererCapabilities:
    supported_emotions: set[Emotion]
    supports_viseme: bool
    supports_gaze: bool
    supports_motion: bool
```

**Core はこれを見て分岐しない。** デバッグ表示と、Content Pack の検証（「このモデルはこの表情に対応していません」の警告）に使う。

---

## HitRegion

```python
@dataclass(frozen=True)
class HitRegion:
    """クリックスルーのヒットテスト用。Shell に shell.* で渡される。"""
    polygon: list[tuple[float, float]]   # ウィンドウ座標
    updated_at: float
```

### なぜ Renderer が返すのか

キャラクターの当たり判定は**描画結果から決まる**。VRM のポーズやカメラ角度で変わるため、Core は知らない。

Stage が Renderer から取得し、`shell.*` で Shell に渡す。Shell はこれを使ってカーソル位置を判定し、クリックスルーを切り替える（[../architecture/ui.md](../architecture/ui.md)）。

---

## 実装

| Renderer | Phase | ライセンス |
|---|---|---|
| `VRMRenderer` | **0-1** | `@pixiv/three-vrm` は **MIT** |
| `Live2DRenderer` | 9 | **Cubism Core は非OSS**。ビルド時取得、リポジトリに入れない |

### VRM を先にする理由

`@pixiv/three-vrm` が MIT で、**リポジトリにもバイナリにも非OSS物が混ざらない**。

これにより:
- OSS 化の障害がゼロ
- 第三者 Extension 配布の障害がゼロ
- ビルドに外部ダウンロードが不要（開発体験が良い）

### Live2D 導入時の扱い

AIRI と同じパターンを採る。

```
Cubism Core を .gitignore し、ビルド時に取得する
  → リポジトリには非OSS物が入らない
  → ただし配布バイナリには同梱される
  → Live2D 社のライセンス条件（売上規模による区分、表示義務）が別途適用される
```

**Phase 9 で配布形態ごとに確認する。** Extension 境界に隔離してあるので、問題があれば Live2D を落とせる。

---

## Core 側の合成

**最終的な `ExpressionIntent` は Core が決める。合成規則の定義は [../architecture/ui.md](../architecture/ui.md) §3。**

Renderer 側の契約は1行:

> **Renderer は合成に関与しない。** 受け取った意図をそのまま表現し、表現できなければ自分でフォールバックする。

---

## テスト

| # | テスト |
|---|---|
| 1 | `Emotion` enum が1箇所にだけ定義されている（静的検査） |
| 2 | Renderer が未知の emotion をフォールバックする |
| 3 | Core が `capabilities()` を見て分岐していない（静的検査） |
| 4 | 表情が Mood + ACT の合成になる（Core 側） |
| 5 | `duration_ms` 指定で自動的に戻る |
| 6 | `blend_ms` で遷移が補間される |
| 7 | リップシンクが無音時に口を閉じる |
| 8 | `hit_region()` がポーズ変化に追従する |
| 9 | Content Pack が対応していない表情を指定したら警告が出る |
| 10 | Renderer を差し替えても Core のコードが変わらない（Phase 9 で検証） |

**10 が本当の検証。** それまで抽象の正しさは仮説のままである。
