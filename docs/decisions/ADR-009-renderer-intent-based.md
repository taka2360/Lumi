# ADR-009: Character Renderer を「意図」ベースとし VRM 優先とする

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../interfaces/renderer.md](../interfaces/renderer.md), [../architecture/ui.md](../architecture/ui.md) |

---

## Decision

**2つの決定を1つの ADR にまとめる**（互いに強く関連するため）。

### 1. `CharacterRenderer` は「意図」を受け取る。パラメータではない

```python
character.set_expression(ExpressionIntent(emotion=Emotion.HAPPY, intensity=0.7, blend_ms=300))
```

Renderer が表現できない意図は、**Renderer 側で**最も近い意図にフォールバックする。Core は知らない。

### 2. VRM を第一実装とし、Live2D は Phase 9 の RendererExtension とする

---

## Reason

### 表情モデルが根本的に異なる

| | VRM | Live2D |
|---|---|---|
| モデル | 名前付きブレンドシェイプの合成（`happy` / `aa` / `blink`） | 生パラメータの直接操作（`ParamEyeLOpen` / `ParamMouthForm`） |
| 抽象度 | 高い（`happy` が定義されている） | 低い（`happy` は複数パラメータの組み合わせ） |

`CharacterRenderer` がパラメータを露出すると、**Live2D 追加時に必ず破綻する**。Core が VRM のブレンドシェイプ名を知っていたら、Live2D では意味をなさない。

### AIRI は実際にこの問題を抱えている

AIRI は `stage-ui-three`（VRM）と `stage-ui-live2d` を**並列な独立パッケージ**として実装しており、共通の抽象基底を持たない。結果:

- `Emotion` enum が **4箇所に重複定義**されている（`stage-ui`, `stage-ui-live2d`, `stage-ui-mmd`, `stage-ui-spine`, さらに `pipelines-audio` に5つ目）
- マッピングテーブルが微妙に異なる（`stage-ui-live2d` 版は `Think`/`Awkward`/`Question`/`Neutral` の VRM 表情が `undefined`）
- Live2D は `Map<name, value>` の直接パラメータ操作、VRM は名前付き感情プリセット → 抽象度が揃っていない

**Lumi は最初から抽象を1つ持つ。** `Emotion` enum は1箇所にだけ定義する（静的検査で保証）。

### VRM を優先する理由 — ライセンス

| | VRM | Live2D |
|---|---|---|
| ライブラリ | `@pixiv/three-vrm` = **MIT** | `pixi-live2d-display` は OSS だが… |
| ランタイム | 追加物なし | **Cubism Core = 非OSS** |
| リポジトリ | 非OSS 物が入らない | ビルド時取得が必要（`.gitignore`） |
| 配布バイナリ | クリーン | **Cubism Core が同梱される** |

VRM 優先により:
- OSS 化の障害がゼロ
- **第三者 Extension 配布の障害がゼロ**
- ビルドに外部ダウンロードが不要（開発体験が良い）

これは承認済みの方針（Core = MIT、非OSS は Extension・外部プロセス境界に隔離）と整合する。

---

## Alternatives

### A. パラメータを露出する

```python
renderer.set_parameter("ParamEyeLOpen", 0.3)
```

**利点:** 表現の自由度が最大。細かい制御ができる
**欠点:** Core が Renderer 固有のパラメータ名を知ることになる。**Live2D 追加時に全面書き換え**

### B. Renderer ごとに別の API を持つ（AIRI の実質的な状態）

**利点:** 各 Renderer の機能をフルに使える
**欠点:** Core が Renderer の種類で分岐する。enum の重複定義。**Renderer 追加のたびに Core が変わる**

### C. Core が `capabilities()` を見て分岐する

```python
if Emotion.CURIOUS in renderer.capabilities().supported_emotions:
    renderer.apply_expression(curious)
else:
    renderer.apply_expression(think)
```

**利点:** フォールバックが明示的
**欠点:** **分岐が Core に入る。** Renderer が増えるたびに Core が変わる。Renderer 側でフォールバックする方が正しい

### D. Live2D を先に実装する（当初の要望）

**利点:** 「伺か」の系譜に近い見た目。日本語圏のモデル資産が豊富
**欠点:** 非OSS の Cubism Core がビルドと配布に絡む。OSS 化・第三者配布の障害になる

**→ ユーザー承認により VRM 優先を採用。** Live2D は Phase 9 で RendererExtension として追加する。

### E. 両方を Phase 1 で並行実装

**利点:** 抽象の妥当性が早期に検証される
**欠点:** **MVP が確実に肥大する。** Phase 1 は「話しかけると答える」に集中すべき

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| 表現の自由度が下がる | 細かいパラメータ制御ができない |
| **抽象の妥当性が Phase 9 まで検証されない** | 2実装で試すまで仮説のまま |
| Live2D が後回しになる | 日本語圏のモデル資産をすぐ使えない |

### 抽象が間違っていたときのリスク

**これは実在するリスクである。** Phase 9 で Live2D を追加したとき、`ExpressionIntent` では表現しきれないことが判明する可能性がある。

緩和:
- `ExpressionIntent` に `intensity` と `blend_ms` を持たせ、最低限の制御を残す
- Content Pack の `expressions.toml` で「意図 → 具体パラメータ」のマッピングを外出しする
- **Phase 9 で判明したら新しい ADR を書いて interface を改訂する**

「完璧な抽象を今作る」ことは目指さない。設計原則7（過剰な抽象化を避ける）に照らして、**現時点で分かっている範囲で最小の抽象**にとどめる。

---

## Consequences

### `Emotion` enum は1箇所にだけ定義する

静的検査で重複定義を検出する。AIRI の5箇所重複を繰り返さない。

### Core が最終的な意図を合成する

```
Mood（Internal State。持続。慣性と減衰）    ← ベースライン
  +
<|ACT|> マーカー（瞬間値）                  ← この発話だけ
  =
ExpressionIntent → Renderer
```

**Renderer は合成に関与しない。** 受け取った意図をそのまま表現する。

これにより、同じ「驚く」でも機嫌のいいときと悪いときで違って見える。

### `intensity` の上限は Renderer の実装詳細

VRM のブレンドシェイプは 1.0 まで指定できるが、**実際には 0.7-0.8 程度に抑えたほうが自然**（AIRI も同じ結論に達し、値を抑える対処をしている）。

Core は 0.0-1.0 で意図を伝え、**Renderer が実際の値域にマップする**。

### `hit_region()` も Renderer が返す

キャラクターの当たり判定は描画結果から決まる（VRM のポーズやカメラ角度で変わる）。Core は知らない。

Stage が Renderer から取得し、`shell.*` で Shell に渡す。Shell がクリックスルーの判定に使う。

### Live2D 導入時（Phase 9）

AIRI と同じパターンを採る。

```
Cubism Core を .gitignore し、ビルド時に取得する
  → リポジトリには非OSS物が入らない
  → 配布バイナリには同梱される
  → Live2D 社のライセンス条件が別途適用される
```

**Extension 境界に隔離してあるので、問題があれば Live2D を落とせる。**

### Phase 1 では表情が Stretch

**リップシンクだけで会話は成立する。** 詰まったら表情を最初に落とす（[roadmap.md](../roadmap.md)）。

抽象は Phase 1 で定義するが、実装は最小でよい。
