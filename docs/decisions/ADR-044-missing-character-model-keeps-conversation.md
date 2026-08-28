# ADR-044: キャラクターモデルの欠落で会話を停止しない

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-25 |
| 関連 | [../architecture/extension.md](../architecture/extension.md) §9, [../architecture/ui.md](../architecture/ui.md)「キャラクターのモデルは Core が決め、Shell が配信する」, [ADR-029](ADR-029-content-pack-asset-delivery.md) |
| 実装 | `core/lumi/content/pack.py`, `core/lumi/agent/runtime.py` |

## Decision

`character.toml` の `[model]` が指すファイルが Content Pack 内に無い、または Stage で読み込めない場合も、
**人格と音声設定が有効なら Content Pack と Reactive Loop は読み込む。** Stage は既存のプレースホルダを
理由付きで表示し、音声入力と会話は継続する。

モデルについても次の検証は維持する。

- 宣言パスが Content Pack の外を指す場合は拒否する
- `[model.credit]` が欠けている場合は拒否する
- Stage でモデルを取得・解析できなかった理由はプレースホルダとともに表示する

## Reason

モデルは表現層のアセットであり、人格・STT・LLM・TTS のいずれでもない。ファイルが欠けたときに
Content Pack 全体を拒否すると、Stage はプレースホルダを正常に描ける一方、Core は会話用の
`ReactiveLoop` を作らない。結果として「キャラクターは見えるのに話しかけても反応しない」という、
表示と実際の受付状態が矛盾した状態になる。

パス境界とクレジットは安全性・権利処理の条件なので fail-closed のままにする。モデル実体の欠落は
それらと異なり、既に明示的な表示フォールバックがあるため、会話まで停止する理由にはならない。

## Consequences

1. Core はパック内に解決したモデルパスを、ファイルの有無にかかわらず Stage へ配る
2. Shell または Stage で取得・解析に失敗した場合、Stage がプレースホルダと理由を表示する
3. モデル欠落時も Content Pack の人格・話者設定を使って Reactive Loop を開始する
4. 「モデルが無い」と「Content Pack が読めない」は引き続き区別する
