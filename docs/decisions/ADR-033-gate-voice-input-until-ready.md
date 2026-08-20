# ADR-033: 起動フェーズが `ready` になるまで音声入力を開始しない

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-19 |
| 関連 | [../architecture/core.md](../architecture/core.md) §7, [../architecture/ui.md](../architecture/ui.md)「起動フェーズ」, [../architecture/audio.md](../architecture/audio.md) §2 |
| 実装 | `core/lumi/agent/runtime.py` |
| 後続 | **[ADR-034](ADR-034-gate-startup-on-complete-setup.md) が Alternative 3 を採用側に変えた**（`ready` が3要素すべてのウォーム完了を意味するようになったため、音声入力の開始もそこまで下がる）。ゲートが `boot: ready` であること自体は変わらない |

---

## Decision

**Core が `stage.setup.state` で `boot: ready` を配信するまで、マイク入力と Reactive Loop を開始しない。**

Phase 1 の `starting` は TTS エンジンの起動待ちであるため、起動順序を次のようにする。

1. TTS をウォームし、`starting` から `ready` または `failed` へ状態を進める
2. `boot: ready` の配信が完了する
3. Audio I/O を開始し、最初の入力フレームを確認する
4. Reactive Loop を開始する
5. LLM と STT のウォームアップを続ける

TTS を取得しなかった場合や起動に失敗した場合も、既存の起動フェーズ規則どおり
`boot: ready` になる。**音声入力の可否と、Lumi を起動完了にするかは混同しない。**

## Reason

ローディング画面は「Lumi はまだ起動中」という明示である。その裏で音声を受け付けると、
ユーザーからは姿が見えないまま発話だけが処理され、ローディング完了後に応答が現れうる。
画面が示す状態と実際の受付状態が一致せず、入力が届いたかも判断できない。

`ready` を受付ゲートにすれば、**ローディング中は聞かず、キャラクターを表示できる状態になってから聞く**
という単一の境界になる。Stage が独自に判定する必要もなく、決定権は Core に残る。

## Alternatives

### 1. マイクは開いたまま、VAD 通知だけ捨てる

ストリーム開通を先に確認できるが、ローディング中も音声サンプルを取り込み続ける。
「受け付けない」という状態として不明瞭なので採らない。

### 2. Stage がローディング中の入力を無効化する

音声 I/O は Core 内にあり、Stage には止める権限も経路もない。表示層に判断を持たせるため採らない。

### 3. LLM / STT を含む全 Provider のウォーム完了まで待つ

最初のターンは最も安定するが、`boot` の定義は TTS の起動状態であり、画面が `ready` になった後も
聞かない時間が生まれる。今回守る境界は**表示されるローディングの完了**なので採らない。

## Trade-offs

- TTS の起動中は音声入力デバイスを開通確認できない
- `ready` 後に入力ストリームの最初のフレームを待つため、受付開始まで最大で開通確認時間が加わる
- LLM / STT のウォーム中に受けた最初のターンは、従来どおり Provider のロード完了を待つことがある

## Consequences

1. [core.md](../architecture/core.md) §7 の Audio I/O と TTS 起動の順序を入れ替える
2. [ui.md](../architecture/ui.md) の `ready` を音声入力開始のゲートとして明記する
3. `ConversationRuntime` は TTS ウォーム完了後のコールバックで Audio I/O と Reactive Loop を開始する
4. ローディング完了前に Audio I/O が開始されないことを回帰テストで固定する
