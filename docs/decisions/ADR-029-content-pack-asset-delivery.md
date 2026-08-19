# ADR-029: Content Pack のアセットは Shell が配信する

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-08-19 |
| **関連** | [ADR-005](ADR-005-extension-two-mechanisms.md)（Content Pack はデータのみ） / [ADR-028](ADR-028-stage-initiated-request.md)（Stage → Core の要求方向） / [architecture/ui.md](../architecture/ui.md) / [contracts/security-boundaries.md](../contracts/security-boundaries.md) B2・B3 / [licensing.md](../licensing.md) §4.5 |

## Decision

**Content Pack のモデルファイル（`model.vrm`）は Shell が WebView に配信する。**

責任を3つに割る。**この分割は既存の三分割そのものであり、新しい原則ではない。**

| 誰が | 何を | なぜそこか |
|---|---|---|
| **Core** | **どのモデルか**を決め、絶対パスとクレジットを `stage.character.model` で配信する | Content Pack を読むのは Core。**「何を使うか」は判断であり、判断は Core が持つ** |
| **Shell** | そのファイルを WebView に**読ませる**（Tauri の asset protocol + scope 検証） | **ファイルを読むのは OS 特権プリミティブ**。Shell の担当範囲そのもの |
| **Stage** | 受け取った path を描画する | 表現のみ。**どのモデルかを Stage は決めない** |

**scope は Shell が起動時に決める。** Stage が渡した path が Content Pack ディレクトリの外を
指していたら、**Tauri が配信を拒否する**。Stage から来た値を検証せずに OS に渡さない（B2）。

## Reason

**Core をファイルサーバにしないため。**

> Core は権威を持つが、**能力の実装を持たない**。（`.claude/rules/python-core.md`）

「127.0.0.1 のポートに GET 経路を1本足すだけ」は小さく見える。しかし**それは Core に
「ファイルを配信する」という能力を持たせることであり、Core の定義に反する。**
一度その経路ができると、次に配信したいもの（表情マッピング、モーション、Live2D のテクスチャ）が
出るたびに Core が肥る。**能力は Shell か Extension に置く。**

**Stage の dist に焼き込む案を採らなかった理由は、クレジットが嘘になるからである。**
[licensing.md](../licensing.md) §4.5 は「モデル名・作者名は **Content Pack のメタデータが持つ**。
Core にハードコードすると、モデルを差し替えた瞬間にクレジットが嘘になる」と明示している。
Stage のビルド成果物に焼き込むのは、**ハードコードの置き場所を Core から Stage に移しただけ**で、
差し替えたときに表示が追随しないという同じ失敗になる。

**「差し替え可能性がライセンスリスクの緩和策である」という主張は、差し替えたときに表示も
追随して初めて成立する**（[interfaces/provider.md](../interfaces/provider.md) の `attribution()` と同じ論法）。

## Alternatives

### A. Core が自分の WS ポートで HTTP 配信する

経路を1本だけ（path 引数なし・token 必須・127.0.0.1 限定）にすれば、攻撃面は小さい。
`websockets` の `process_request` で実現でき、**CSP は既に `http://127.0.0.1:*` を許可済み**なので
変更が Core だけで完結する。**3言語にまたがらない点は明確な利点だった。**

**採らなかった理由**: 上記のとおり Core の定義に反する。攻撃面の大小ではなく、**責任の置き場所**の問題。

### B. Stage の dist に同梱する

一番小さい。ビルド時に `content/characters/lumi/model.vrm` を `stage/dist/` にコピーすれば、
現在の `DEFAULT_VRM_URL = "/character.vrm"` がそのまま動く。

**採らなかった理由**: Content Pack がモデルを選ぶという設計を捨てることになる。上記。

### C. WS で bytes を送る

**却下。** 25 MB を JSON に載せる経路は、barge-in の critical path と同じ接続を塞ぐ。

## Trade-offs

### 受け入れるコスト

- **変更が3言語にまたがる**（Core の配信 / Shell の scope と CSP / Stage の読み込み）
- **CSP を緩める。** `connect-src` に asset protocol の origin を足す。
  **足すのは asset protocol だけで、`file:` は足さない**
- **dev と本番でファイルの置き場所が違う**（リポジトリの `content/` / 配布物の
  `resources/core/_internal/content/`）。Shell は**両方の候補を試す**。
  `sidecar_candidates()` と同じ構造であり、同じ理由（`tauri dev` でも `resources` が存在する）

### 得るもの

- **Content Pack を差し替えるとモデルもクレジットも追随する。** 焼き込みでは得られない
- **Core が能力を持たない**という定義が保たれる
- **Stage が指定できる範囲を Shell が握る。** Stage が壊れても Content Pack の外は読めない

## Consequences

### 新しい `stage.*` メソッドが1つ増える

```
stage.character.model  { path, format, credit: { name, credit_text, license_name, license_url } }
```

**モデルが無いときも配信する**（`path: null`）。「まだ来ていない」と「モデルが無い Content Pack」は
別の状態であり、Stage は後者を**プレースホルダで、理由を出して**描く（黙って劣化しない）。

### Shell に asset protocol の scope 検証層が増える

B3（`os.*` の検証層）と**同じ性質のもの**。違いは、拒否の判断を Tauri の scope 機構が持つこと。
**scope に入れるのは Content Pack ディレクトリだけ。** ホームディレクトリや `$RESOURCE` 全体を
許可しない。

### `[model]` を宣言する Content Pack はクレジットも宣言する（fail-closed）

`voice.toml` の `[credit]` と同じ規則 → [architecture/extension.md](../architecture/extension.md) §9。
**その license がクレジットを要求するかどうかとは別の判断。** 既定同梱モデル（光莉 / ひかり）は
クレジット表記が不要だが、Lumi は出す。

### 保証しないこと

- **Content Pack の中身が安全であることは保証しない。** `_reject_code` はコードの拡張子を弾くだけで、
  **不正な glTF が three.js を壊す可能性は残る**。Content Pack は現状「信頼して読むもの」であり、
  署名・サンドボックスは Phase 9 の Extension 署名と同じ扱い
- **この経路で配信してよいのは Content Pack のデータだけ。** 記憶 DB・監査ログ・設定ファイルを
  scope に入れない。**入れたくなったら、それは別の ADR が要る**
