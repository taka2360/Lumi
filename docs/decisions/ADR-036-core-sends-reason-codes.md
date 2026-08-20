# ADR-036: Core は表示文字列を送らない。理由はコードで送り、翻訳は Stage が持つ

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-20 |
| 関連 | [../contracts/wire.json](../contracts/wire.json), [ADR-022](ADR-022-wire-contract.md), [ADR-029](ADR-029-content-pack-asset-delivery.md), [../architecture/ui.md](../architecture/ui.md) |
| 実装 | `core/lumi/agent/runtime.py`, `core/lumi/transport/methods.py`, `stage/src/character/loadCharacter.ts`, `stage/src/i18n/index.ts` |

---

## Decision

**Core が線に載せる「理由」は、表示文字列ではなく安定したコードである。**

1. `stage.character.model` の `reason` は `model_not_in_pack` / `pack_unreadable` の
   いずれかのコードを取る。値は [wire.json](../contracts/wire.json) の
   `character_model_reasons` が唯一の定義場所
2. Stage は `i18n` の `character.model.<reason>` で訳文を引く
3. **知らないコードは、コードそのものをそのまま表示する。** 空文字にも
   「不明なエラー」にも丸めない
4. この規則は既存の `SetupError.reason` → `status.failure.<reason>` と同じ形である。
   本 ADR は**その形を Core → Stage の理由一般に広げて明文化するもの**であり、
   新しい機構を足すものではない

## Reason

`runtime.py` は `reason` に日本語のリテラルを入れて送っていた。

```python
reason = "Content Pack がモデルを含んでいない" if pack else "Content Pack が読めない"
```

これは `loadCharacter.ts` の `fallbackReason` としてそのまま画面に出る。すぐ隣の
`character.load.failed` は `translate(locale, ...)` を通っているので、**この1本だけが
locale 設定を無視する。** 英語表示にした利用者の画面に、この行だけ日本語が出る。

ADR-030 で locale の即時反映を入れた意味が、この経路では効いていない。

### なぜ Core 側で翻訳しないのか

**Core は現在の locale を知らないし、知るべきでもない。**

- locale は Stage の設定であり、表示の都合である。Core が持つのは判断・状態・ポリシー・記憶
  （[core.md](../architecture/core.md) §1）
- Core が翻訳するなら、locale が変わるたびに Core に問い直すか、全言語を送るかになる。
  前者は表示のために往復を増やし、後者は Core に翻訳表を持たせる
- 実際 `stage.settings.update` で locale は即時反映されるが、**すでに送信済みの
  `stage.character.model` は再送されない**。Core が翻訳していたら、その1本だけが
  切り替わらずに残る

### 保証しないこと

**これは「Core から日本語が出ない」ことの保証ではない。** `agent/prompt.py` の人格
プロンプトと隔離ブロックの書式は日本語であり、日本語であることに意味がある。本 ADR が
対象にするのは **`stage.*` に載って画面にそのまま出る文字列**だけである。

また、コード化は**網羅性を保証しない**。Stage が知らないコードは訳されずに出る。それは
劣化ではなく、意図した振る舞いである（下記）。

## Alternatives

### A. Core が locale を受け取って翻訳する

利点: 理由の文言が Core 側に集まり、Stage は表示するだけになる。Core のログと画面表示が
一致するので、サポート時に突き合わせやすい。

採らない理由: 上記のとおり Core が表示の都合を持つことになる。locale 変更のたびに
再送が要る状態が増え、送り忘れた1本が古い言語のまま残る。

### B. Core が両言語を送る

利点: 往復が要らず、Stage は選ぶだけでよい。

採らない理由: Core に翻訳表が住みつく。言語を増やすたびに Core が変わる。

### C. 現状維持（日本語のリテラルを送る）

利点: 変更が要らない。日本語利用者には正しく見える。

採らない理由: 英語表示が壊れている、という事実は変わらない。しかも**壊れ方が静か**で、
日本語で開発している限り誰も気づかない。

## Trade-offs

**受け入れるコスト**

- Core が理由を増やすたびに、wire.json と Stage の訳文の2箇所を触る必要がある
- Core のログに出る値と、画面に出る文言が一致しなくなる（ログは `model_not_in_pack`、
  画面は「Content Pack がモデルを含んでいない」）

**得るもの**

- locale 設定がすべての表示に効く
- 未知のコードがそのまま出るので、**契約のずれが画面で見える**。Core が先に新しい理由を
  送り始めても、Stage は空白ではなくコードを出す

## Consequences

- `docs/contracts/wire.json` に `character_model_reasons` が加わり、3言語の contract
  テストが突き合わせる
- **未知コードのフォールバックはテストで固定する。** `?? ""` を書いた瞬間に静かに壊れる
  性質のものなので、目視確認では守れない
- `.claude/rules/01-design-process.md` の言語規則を、この決定に合わせて書き直す
  （「ユーザー向け文言は対象外＝日本語のままでよい」は、Core が表示文字列を持たなく
  なった以上もう正しくない）
- 今後 Core が `stage.*` に理由を載せるときは、**必ずコードにする**。ADR-034 で
  `blocked` 画面に並ぶ理由が増えることが分かっており、そこが最初の適用先になる
