# ADR-043: ユーザーが記憶 UI で書き直した文は `user_confirmed` になる

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-08-23 |
| **関連** | [ADR-011](ADR-011-provenance-no-laundering.md), [ADR-042](ADR-042-panel-windows-and-panel-role.md), [contracts/invariants.md](../contracts/invariants.md) Invariant 7, [contracts/provenance.md](../contracts/provenance.md), [architecture/memory.md](../architecture/memory.md) §8 |

## Decision

**記憶 UI の「直す」（`MemoryStore.rewrite()`）が書いたレコードは `user_confirmed` / `TRUSTED` になる。**

Invariant 7 の「昇格してよい箇所」の記述を、**関数名ではなく経路**で書き直す。

| # | 場所 | 何をするか |
|---|---|---|
| 1 | ユーザー直接入力ハンドラ（`agent/session.py`） | 発話に `TRUSTED` を付ける |
| 2 | **`MemoryStore._confirm_in()`** — `confirm()` と `rewrite()` から呼ばれる | **昇格** |

**昇格の代入そのものは、いまも1箇所にしかない。** 変わったのは呼ぶ側が2つになったことで、
どちらも**記憶ウィンドウでユーザーが押したボタン**である。

## Reason

**Invariant 7 が禁じているのは「自動処理による昇格」であって、人間による昇格ではない。**
不変条件の本文は「`tainted → trusted` への昇格は、**人間の明示的な確認を経た場合にのみ**発生する」であり、
**ユーザーが自分で文を打ち直すことは、確認より弱い根拠ではない。**

昇格しない設計にすると、次が起きる。

```
Web ページ由来の記憶（tainted）
  → ユーザーが「それ違う。正しくはこう」と自分で書き直す
  → 書き直した文が tainted のまま残る
```

**ユーザー自身が書いた1文が「外部由来・未確認」として扱われる。**
これは汚染の伝播ではなく、**汚染の誤検出**である。記憶 UI は
`tainted → trusted` の唯一の昇格経路として設計されており（[architecture/memory.md](../architecture/memory.md) §8）、
そこで一番強い操作である「書き直す」だけが昇格できないのは筋が通らない。

**押せるのは人間だけである**ことは Invariant 8 が支えている。記憶ウィンドウは Lumi の
ウィンドウであり、`WindowKind::is_protected()` が全ウィンドウに対して true を返すので、
`os.input.*` の対象にできない。**この依存は [architecture/ui.md](../architecture/ui.md) §5b に明記してある。**

## Alternatives

**`rewrite()` は昇格せず、ユーザーが別途「これで合っている」を押す。**
利点: Invariant 7 の記述を一切変えずに済む。採らない理由: 上記の誤検出が残り、
かつ**同じ意図に2回の操作を要求する**。押し忘れれば、ユーザーが書いた文が
「Lumi が外から拾ってきた未確認の情報」として扱われ続ける。

**`rewrite()` の中に `TRUSTED` の代入をもう1つ書く。**
採らない理由: 静的検査が数える代入箇所が増える。
**`_confirm_in()` を1つに保つほうが、検査の意味が強い。**

**編集を「削除 + 新規作成」にする。**
利点: 昇格の話が消える。採らない理由: **supersede の履歴が切れる。**
「前はこう言ってたよね」の根拠が、訂正のたびに失われる。

## Trade-offs

**受け入れるコスト**
- Invariant 7 の実装規則（「2箇所」）の**書き方**が、関数名から経路に変わる
- 昇格の呼び出し元が2つになる。**代入は1つのまま**だが、レビューで確認する箇所は増える

**得るもの**
- ユーザーが書いた文が、ユーザーが書いた文として扱われる
- 訂正が1操作で終わる
- 履歴が切れない（supersede のまま）

## Consequences

- [contracts/invariants.md](../contracts/invariants.md) Invariant 7 の表と、
  `.claude/rules/00-invariants.md` の「2箇所」の記述を経路の書き方に更新する
- **静的検査は変わらない。** 検査しているのは「`trust_level = TRUSTED` の代入がある**ファイル**」であり、
  `memory/store.py` は元から入っている
- [interfaces/memory.md](../interfaces/memory.md) の `confirm()` / `rewrite()` の記述を揃える
