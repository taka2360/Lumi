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

### 生成方式〔Provisional。Phase 0-1 で決める〕

| 方式 | 評価 |
|---|---|
| TTS 出力から音素タイミングを取得 | 精度が高い。TTS エンジンが対応していれば |
| 再生中の音声から推定 | エンジン非依存。AIRI は wLipSync を使用 |

**アタック/リリースの非対称スムージングと無音判定は必須。** これが無いと口がガクガクする。

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
