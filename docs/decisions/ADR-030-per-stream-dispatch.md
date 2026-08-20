# ADR-030: DomainEvent の配送を stream ごとに直列化し、同一 stream への再入 publish を拒否する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-19 |
| 関連 | [../contracts/event-model.md](../contracts/event-model.md)「順序保証」/「採番責任」, [ADR-010](ADR-010-signal-vs-domain-event.md) |
| 実装 | `core/lumi/kernel/event.py` |

---

## Decision

**`EventBus` は同一 `stream_key` の配送も直列化する。** 採番・永続化だけでなく、
`_dispatch` が終わるまでロックを保持する。

**その帰結として、同じ Task が配送中の同一 `stream_key` へ直接 publish する場合だけ禁止する。**
`EventBus` は配送中の stream と Task を記録し、同じ Task からの直接再入を検出したら
`ReentrantPublishError` を投げる。**待たせない**（`asyncio.Lock` は再入不可なので、
その Task を lock 待ちにすると自分自身の配送完了を待つ無言のデッドロックになる）。
別コルーチンからの同一 stream publish は再入ではなく、lock を待って許可される。
他の stream への publish も従来どおり自由である。

## Reason

### 契約は既に「配送も」と書いていた

[event-model.md](../contracts/event-model.md)「順序保証」は
**「同一 `stream_key` 内では順序を保証する（配送も処理も）」** と書いている。
しかし同じ文書の実装イメージは `async with lock:` の**外**で `await self._dispatch(event)` を
呼んでおり、**文書が自分の契約を満たさない例を載せていた。** 実装もそれに従っていた。

### 何が壊れるか

`publish` がロックを解いた後に配送するので、次のように追い越しが起きる。

```text
コルーチン A: publish(seq=1) → ロック解放 → dispatch(1) → handler(1) が await で中断
コルーチン B: publish(seq=2) → ロック解放 → dispatch(2) → handler(2) が完走
             → handler(1) が再開
```

購読者から見ると **2 が 1 より先に届く。** 消費側は
「欠番・逆転を検出したら**エラーにする**」ことになっている（同文書）ので、
`SequenceChecker` は正しく `SequenceError` を投げる。
**正しい検出器が、正しくない配送を報告し続ける**状態になる。

Phase 1 で購読しているのは Inspector だけだが、**これは Bus の契約の問題**であり、
購読者が増えてから直すものではない。順序が要らない購読者は自分で無視できるが、
順序が要る購読者は Bus が保証しない限り自分では回復できない。

### なぜ再入を「禁止」するのか

配送中にロックを保持すると、**同じ Task の**購読者が同じ stream へ publish した瞬間にデッドロックする。
選択肢は3つあった。

**同じ Task からの直接再入を許すこと自体が矛盾している。** 購読者が `seq=1` を処理している最中に発行する
`seq=2` は、定義上 `seq=1` の処理の**後**に順序づけられなければならないが、
その処理はまだ終わっていない。同じ Task が lock を待てば自分自身を待つため、
**「同一 stream 内では処理も順序保証する」と両立しない。** 別コルーチンは lock を待てるので、
この禁止の対象ではない。

だから禁止する。そして**禁止は明示的な例外で表す。** 無言のデッドロックは
「黙って劣化しない」に真正面から反する（画面上は Lumi が固まるだけで、理由がどこにも出ない）。

## Alternatives

### 1. 再入可能ロックにする

同じ Task の購読者が同じ stream へ publish できる。**既存コードを何も壊さない**のが利点。

採らない理由: 上記のとおり**順序が定義できない。** 再入を許した時点で
「処理も順序保証する」は嘘になり、契約から意味が失われる。

### 2. 配送はロックの外のまま、購読者側に順序整列を任せる

Bus が単純なままで済む。`SequenceChecker` は既にあるので、
**バッファして並べ替えれば購読者は自衛できる。**

採らない理由: **順序保証を Bus の責任から購読者の責任へ移すことになる。**
購読者が増えるたびに同じバッファを書き、書き忘れた1つが静かに壊れる。
契約が「Bus が保証する」と書いてある以上、Bus が保証する。

### 3. 契約を「配送順は保証しない」に緩める

実装を変えずに済む。

採らない理由: `ActivityStarted` → `ActivityEnded` の順序は
Inspector の表示にも将来のリプレイにも要る。**緩めた先に失うものが大きすぎる。**

## Trade-offs

### 受け入れるコスト

- **遅い購読者が同じ stream の publish を待たせる。** 購読者はもともと `await` されており
  待ちは発生していたが、その待ちが**次の publish にも及ぶ**ようになる
- 同じ Task の購読者から同一 stream へ publish できなくなる（**Phase 1 に該当箇所は無い**）。別コルーチンは lock 待ちで publish できる
- 検出のために「いまどの stream を、どのタスクが配送中か」を Bus が持つ

### 得るもの

- **契約と実装が一致する。** 文書の実装イメージも直る
- 購読者は順序整列を書かなくてよい
- 再入は**デッドロックではなく例外**になる。原因が最初の1回で分かる

### 保証しないこと

- **異なる `stream_key` 間の順序は依然として保証しない**（元からの契約）
- 購読者が別 stream 経由で間接的に同一 stream へ publish する経路までは検出しない。
  検出するのは**同一タスクが配送中の stream へ直接 publish した場合**だけである
- 購読者が失敗しても Bus は止まらない（元からの規則）。再入の例外も
  `event.subscriber_failed` として記録され、他の購読者への配送は続く

## Consequences

1. `EventBus.publish` は `_dispatch` を含めてロック内で実行する
2. `ReentrantPublishError` を追加する。同じ Task の購読者から配送中の同一 stream へ直接 publish すると送出される
3. [event-model.md](../contracts/event-model.md) の実装イメージと「順序保証」の節を更新する
4. 同文書のテスト表に、遅い購読者がいても同一 stream の順序が保つことを確かめる項を足す
