# ADR-048: 生成設定（sampling）を Core が用途ごとに決め、モデルファイルに委ねない

| | |
|---|---|
| Status | Accepted |
| Date | 2026-09-02 |
| 関連 | [../interfaces/provider.md](../interfaces/provider.md) `LLMOptions`, [ADR-008](ADR-008-provider-abstraction.md), [ADR-023](ADR-023-llm-runtime-and-model-acquisition.md), [../measurements/phase2.md](../measurements/phase2.md), [../measurements/phase1.md](../measurements/phase1.md) |
| 実装 | `core/lumi/providers/llm/sampling.py`, `core/lumi/providers/llm/base.py`, `core/lumi/providers/llm/ollama.py`, `core/lumi/agent/runtime.py`, `core/lumi/agent/reactive.py`, `core/lumi/agent/reflection_scheduler.py` |

## Decision

**生成設定は Core が決める。** 用途（`Purpose`）とモデル系列の組に対して `LLMOptions` を一つ返す
`sampling.options_for(model, purpose)` を唯一の組み立て口とし、呼び出し側が `LLMOptions` の
フィールドを個別に書くことを禁じる。値の表は
[../interfaces/provider.md](../interfaces/provider.md) が単一定義とする。

**送らなかったフィールドは「中立」ではなく「モデルファイルに決めさせる」を意味する。**
したがって Lumi が測定したモデル系列に対しては、**既定値と一致する値も含めて全項目を明示的に送る。**
測定のないモデルには temperature だけを送り、残りはそのモデルの作者の値を残す。

会話は `Purpose.CONVERSATION`、記憶抽出は `Purpose.EXTRACTION` を使う。
`set_llm_model()` は**プロファイル全体を引き直す**（モデル名だけ差し替えない）。

`seed` は `LLMOptions` に持つが**製品経路では設定しない。** 開発時の A/B 比較——
同じ入力に対する変種の差を、引きの差から切り離して見るため——のためだけに存在する。

★ **抽出には出力トークン上限を入れない。** 会話の `max_tokens = 512` は暴走の保険だが、
抽出で同じことをすると保険ではなく**新しい失敗様式**になる。抽出は次のパスでも同じ
Episode を読むので、**入力だけで決まる打ち切りはその入力に対して毎回再発する**——
watermark が進まないまま、アイドルのたびに同じ推論を焼き続ける。
打ち切られた抽出を watermark に対してどう扱うかは記憶層の決定であり、本 ADR の範囲ではない。

## Reason

**Lumi は `temperature` しか送っていなかった。** 残りは Ollama がモデルの Modelfile から埋めており、
`qwen3.5:9b` はそこに `temperature 1 / top_k 20 / top_p 0.95 / presence_penalty 1.5` を持っている
（2026-09-02、`/api/show` と、明示送信と省略の出力が seed 固定で完全一致することの両方で確認）。
**つまり3つの sampling パラメータを、Lumi が読んだことのないファイルが決めていた。**
コード上のどこにもその事実は書かれておらず、`temperature=0.8` という1行だけが見えていた。

そのうち `presence_penalty = 1.5` は Qwen 自身の推奨値であり、**その推奨が向けられている用途に対しては正しい**
——長い生成が無限反復に落ちるのを防ぐための値である。**1〜2文の音声応答はその逆の用途である。**
罰則は直近 `repeat_last_n`（64）トークンに対して加算的に効くので、短い応答では助詞・語尾・
**ユーザーが今言ったばかりの語**——日本語で繰り返されて当然の部分——に対して支払われる。
Qwen のモデルカード自身が「高い値は言語混在と性能低下を招くことがある」と警告している。

実測でそのとおりのものが出た（[../measurements/phase2.md](../measurements/phase2.md)）。
`presence_penalty = 1.5` では `clean な履歴`（言語混在）、`早く休んでお休みして`（冗長）、絵文字
（`SPEECH_PROTOCOL` が禁じている）が出る。現行の A では `ゆっくり休んでごきげんよろしく？` /
`何かあったことある？` のように**日本語として壊れた**文が出た。

**記憶抽出は別の用途である。** `phase2.md` の抽出の数値は temperature 0.2 で取ったものだが、
コードは会話と同じ 0.8 で動かしていた。同じ文書が記録している「subject と assertion_mode が
実行ごとに揺れる」は、少なくとも一部はこれで説明がつく。加えて抽出出力は JSON であり、
**`"subject"` というキーが再び現れることを罰するのは形式そのものを罰することである。**

**用途を分けるのはモデルを分けるより先に来る。** 「9B が悪いのか設定が悪いのか」は、
設定がモデルファイル任せである限り切り分けられない。

## Alternatives

| 選択肢 | 利点 | 採らなかった理由 |
|---|---|---|
| Qwen のモデルカードをそのまま採用（`presence_penalty 1.5`） | 提供元の推奨に従うのが最も説明しやすい | **推奨は長文生成に向けたもの。** 短い音声応答では言語混在と冗長化として現れた（実測） |
| `presence_penalty` を 0.5 に落とす（中間） | 反復対策を完全には捨てない | 反復は**そもそも起きていなかった**（10ケースで反復指標はほぼ 0）。効果の無い罰則の代償として応答が伸びただけ |
| Modelfile を Lumi 側で作り直す（`ollama create`） | Ollama の層で一括指定できる | **モデル取得が Lumi の派生モデル作成を伴う**ことになり、ADR-037 の「同意した pull」の外に出る。設定がコードから見えないままなのも変わらない |
| `OllamaProvider` がモデル名を見て決める | Provider に閉じる | **判断が Provider ごとに複製される。** 用途に対する設定は権威側（Core）の決定であり、ADR-008 の分担に反する |
| 全モデルに Qwen の値を適用する | 分岐が無い | 測定していないモデルに他社の測定値を当てるのは、**Ollama に委ねるのをやめて Lumi が根拠なく決める**だけで、改善ではない |
| プロンプトで日本語品質を縛る | 実装が要らない | 出力規約は既に `SPEECH_PROTOCOL` にある。**壊れた日本語はプロンプト違反ではなく復号の結果**であり、文言を足しても直らない |

## Trade-offs

**受け入れるコスト。**

- モデル系列ごとの表を持つ。新しい系列を測るまでは generic に落ちる（= temperature だけ）
- 「Qwen の推奨と違う」1点を抱える。根拠は実測にあり、モデルカードにはない
- `num_predict = 512` は暴走時の保険であり、**発話の途中で切れうる。** 実測 30〜120 トークンに対して
  約4倍の余裕を取っているので、正常に停止するモデルでは発火しない
- **抽出は上限が無いままである。** 暴走した抽出は会話と違って誰も待っていないが、
  アイドル時間と VRAM は焼く。上限を入れるには先に「打ち切られた抽出と watermark」を
  決める必要があり、それは別の変更である

**得るもの。**

- 生成設定がコード上で読める。**「今どの値で喋っているか」がファイル1つで分かる**
- モデルを差し替えても設定が付いてくる（`set_llm_model` が引き直す）
- 抽出と会話が別の設定で動く。記憶の安定性が会話の温度に引きずられない

## Consequences

- `LLMOptions` に sampling フィールドが増える。**`None` は「送らない = モデルファイルに委ねる」**という
  意味を持つようになった。この意味は `providers/llm/base.py` と本 ADR に書かれている
- `OllamaProvider` は `_OPTION_NAMES` の表を通してのみ `options` を組み立てる。
  **Lumi が握っている復号の決定はその表が全部である**
- 将来の `OpenAICompatProvider` などは同じ `LLMOptions` を自分の語彙に写す。
  プロファイルは Provider に依存しない
- **新しいモデル系列を採るときは、先に A/B を取ってからプロファイルを書く。**
  測定手段そのものはこの決定の一部ではない（今回の数値は使い捨ての実験スクリプトで取った）。
  再実行できる形にするかは別途決める
