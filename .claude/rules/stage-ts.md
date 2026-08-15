---
paths:
  - "stage/**/*.ts"
  - "stage/**/*.tsx"
  - "stage/**/*.css"
---

# Stage — React + TypeScript + Zustand

設計 → [architecture/ui.md](../../docs/architecture/ui.md), [interfaces/renderer.md](../../docs/interfaces/renderer.md)

## Stage の責務

**表現のみ。ビジネスロジックを持たない。**

VRM 描画 / 表情・モーション・リップシンク / 吹き出し / Widget ホスト + Broker / 設定 UI（**保存は Core**） / 権限プロンプト / Inspector。

> **判定基準: Stage のストアから読める値は、すべて Core が `stage.*` で配信したものであるべき。**
> Stage が自分で計算して状態を作っていたら、それはロジックが漏れている。

AIRI は Pinia ストアに Agent オーケストレーション・記憶・自律スケジューラを同居させている。**同じことをしない。**

## 2つの経路を混同しない

| namespace | 経路 | 内容 |
|---|---|---|
| `shell.*` | Tauri IPC | ウィンドウのドラッグ、クリックスルー、ホバー。**1ms 以下であるべきもの** |
| `stage.*` | WS（Core） | キャラクターの発話・表情・Widget・設定 |

> **`shell.*` は絶対に AI の判断を運ばない。`stage.*` は絶対に OS 特権を要求しない。**

**`stage/` から `os.*` の型を参照しない**（静的検査で保証する）。

## Stage は信頼されていない（B1 / B2）

Stage が乗っ取られても、**できるのは「変な表情をする」「変な吹き出しを出す」までであるべき**。
XSS・Widget の sandbox 脱出・依存パッケージのサプライチェーンを想定する。

## Renderer は「意図」を受け取る

**パラメータではない。** VRM と Live2D は表情モデルが根本的に異なる。

```ts
applyExpression(intent: ExpressionIntent)   // emotion / intensity / blend_ms
```

- **`Emotion` enum は1箇所にだけ定義する。** Renderer ごとに再定義しない（AIRI は4箇所に重複定義して壊れている）
- 表現できない意図は **Renderer 側でフォールバック**する。Core は `capabilities()` を見て分岐しない
- **合成（Mood + `<|ACT|>`）は Core が行う。Renderer は関与しない**
- `intensity` は 0.0-1.0 で受け取り、実際の値域（0.7-0.8 程度に抑える）へのマップは Renderer の実装詳細

## Widget

**iframe を信用しない。真のセキュリティ境界は Widget Broker。**

- `sandbox="allow-scripts"` のみ。**`allow-same-origin` を併用しない**（iframe が自身の sandbox 属性を外せる）
- ただし sandbox は**多層防御の一枚であって境界ではない**
- **Broker は Core をバイパスして何かを実行しない**（Invariant 2）
- Broker は Stage の他のコードから直接呼べないようにする（メッセージ経路を1本に絞る）

## リップシンク

アタック / リリースの**非対称スムージング**と無音判定は必須。無いと口がガクガクする。
`<|ACT|>` マーカーは**音声化前に除去**する。パース失敗時はマーカーごと落とす（読み上げない）。
