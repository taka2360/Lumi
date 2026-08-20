# ADR-031: `request` 経路が許す副作用を「Core が所有する状態の変更」に限定して定義する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-19 |
| 関連 | [ADR-028](ADR-028-stage-initiated-request.md)（この ADR が受理条件4の文言を修正する）, [../contracts/invariants.md](../contracts/invariants.md) Invariant 1・2, [../contracts/security-boundaries.md](../contracts/security-boundaries.md) B2, [../architecture/core.md](../architecture/core.md) §3・§6b |
| 実装 | `core/lumi/transport/server.py`, `core/lumi/agent/runtime.py` |

---

## Decision

[ADR-028](ADR-028-stage-initiated-request.md) の受理条件4「**副作用を持つ操作を含まないこと**」を、
次のように読み替える。**条件そのものを緩めるのではなく、「副作用」が何を指していたかを確定する。**

> **4. Tool を要する副作用を含まないこと。**
> この経路が変更してよいのは **Core が所有する状態だけ**である。
> **OS・外界・他プロセスに触れるものは例外なく `ToolRegistry.invoke` を通る**（Invariant 2）。

| 変更してよい | 変更してはいけない |
|---|---|
| Core が所有する設定（`lumi.settings.KEYS` にあるキー） | ファイル・プロセス・入力・画面・ネットワーク（**Tool の担当**） |
| その結果の Stage への再配信 | `trust_level` の昇格（Invariant 7） |
| | Activity / Tool の状態遷移（Invariant 4 / Tool Registry の独占） |

**設定ファイルへの書き込みはこの経路で許される。** ただし
**キーの集合は `lumi.settings.KEYS` に固定**され（未知のキーは `UnknownSetting` で拒否）、
値は閉じた選択肢のものだけ検証される（[core.md](../architecture/core.md) §6b）。
**「設定を書ける」であって「ファイルを書ける」ではない。** パスは Stage が指定しない。

## Reason

### ADR-028 は自分の条件4に違反していた

ADR-028 は条件4に「副作用を持つ操作を含まないこと」と書きながら、同じ文書の末尾で
「Phase 1 でこの経路に載るのは **Core が所有する設定の変更だけ**である」と書いている。
**設定の保存はディスクへの書き込みであり、副作用である。**

実装（`stage.settings.update` → `settings.save`）は後者に従っている。
つまり**唯一登録されている method が、受理条件4の字面に反している。**

### 字面どおりに読むと、この経路は何もできない

「副作用ゼロ」を厳密に適用すると、この経路は読み取り専用になる。
ADR-028 が解こうとした問題そのもの——**「押しても何も起きない操作子を置くのは、
機能が無いことより悪い」**——に戻る。**条件4の意図は「読み取り専用」ではなかった。**

### 守りたかったのは Invariant 2 である

条件4の直後に置かれた「やってはいけないこと」は
**「Tool を呼ばない」「trust_level を上げない」「Activity / Tool の状態遷移を起こさない」**の3つで、
どれも**「Core の権威を迂回しない」**という話である。
「いかなる状態も変えない」ではなく、**「Tool Gate を迂回する副作用を持ち込まない」**が意図だった。

**曖昧なまま残す方が危険である。** 次に inbound method を足す人は、条件4を
「ディスクに書くのは前例があるから良い」とも「副作用は禁止と書いてある」とも読める。
**境界の設計で最も避けたいのは「読めば分かるが、読まないと間違える」構造**（ADR-028 自身の言葉）である。

## Alternatives

### 1. 設定の保存を Tool にする

`settings.update` を `ToolRegistry` 経由にすれば、条件4を字面どおり保てる。
**Invariant 2 の例外を1つも作らない**のが利点。

採らない理由: **Permission Kernel は「ユーザーの操作」を認可する仕組みではない。**
Tool の認可は「Lumi がやってよいか」を判定するものであり、
ユーザーが自分の設定を変えることに `ask` を出すのは意味が反転している。
Tool 化すると、`SecurityScope` も `Canonicalizer` も `BindVerifier` も
**設定キーのために作ることになる**（設計原則7「将来使うかもしれないで抽象層を足さない」に反する）。

### 2. 設定変更を Core → Stage の `command` にする（Core が定期的に聞く）

ADR-028 の Alternatives 1 と同じ。**同じ理由で採らない**（ポーリングで要求方向を模倣するだけ）。

### 3. 文言を直さず、実装を「例外」として運用する

**何もしなくて済む。**

採らない理由: 例外は記録されない限り前例になる。
次に「これも Core 所有だから」で通るものが増え、**どこまでが例外か誰も言えなくなる。**

## Trade-offs

### 受け入れるコスト

- **Invariant 2 の適用範囲を明文で狭める。** 「副作用は必ず Tool を通る」ではなく
  「**外界に触れる副作用**は必ず Tool を通る」になる。この線引きは今後も守る必要がある
- 「Core が所有する状態」という語の解釈が新たな判断点になる。
  **迷ったら Tool 側**（fail-closed）

### 得るもの

- 受理条件4が実装と一致する
- 次に inbound method を足す人が、**読まなくても間違えない**基準を持つ
- Invariant 2 が守っている対象（Tool Gate）が明確になる

### 保証しないこと

- **この経路の安全性は「登録した method の危険度の上限」でしか保証されない**（ADR-028 のまま）
- **Core が所有する状態なら何でも書いてよい、という意味ではない。**
  Phase 1 で登録されているのは `stage.settings.update` の1つだけであり、
  追加は常に明示的な登録という行為を要する

## Consequences

1. [ADR-028](ADR-028-stage-initiated-request.md) の冒頭に、この ADR が条件4の文言を修正した旨を追記する
2. [core.md](../architecture/core.md) §3 の受理条件表を、この定義に合わせる
3. **この経路に Tool 実行を載せるときは、依然として新しい ADR を要求する**（ADR-028 Consequences 6 は生きている）
