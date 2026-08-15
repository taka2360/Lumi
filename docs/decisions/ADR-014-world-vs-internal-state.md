# ADR-014: World State と Internal State を分離する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../architecture/world-state.md](../architecture/world-state.md), [../architecture/autonomy.md](../architecture/autonomy.md) |

---

## Decision

**「外界について Lumi が知っていること」と「Lumi 自身がどうであるか」を別のストアにする。**

| | **World State** | **Internal State** |
|---|---|---|
| 由来 | Sensor による**観測** | Lumi 自身の**経験の蓄積** |
| 失効 | **TTL で失効する**（「知らない」に戻る） | 失効しない。減衰・遷移する |
| confidence | **ある** | ない |
| 書き手 | Core（Sensor Signal を受けて） | Core 内部のみ |
| 例 | `user.present`, `desktop.fullscreen` | `mood`, `fatigue`, `drives` |

Memory を加えた**三分離**を原則とする。

```
World State    外界の観測      安い / derived / 失効する
Internal State 自分の状態      安い / 蓄積される / 失効しない
Memory         覚えていること   高い / curated / 減衰する
```

---

## Reason

### 失効の意味が違う

「ユーザーが在席しているか」は**観測**であり、観測が古くなれば「分からない」に戻るべき。

「Lumi の機嫌」は観測ではなく、**古くなっても「分からない」にはならない**。Sensor が止まっても Lumi は自分の機嫌を知っている。

TTL を持つストアに mood を入れると、**「機嫌が失効して分からなくなる」**という意味不明な状態が発生する。

### 判定基準

> **「観測できなくなったら『分からない』になるか?」**

| | 分からなくなる | 分類 |
|---|---|---|
| ユーザーが在席か | Sensor が止まれば分からない | World |
| 前面アプリ | 同上 | World |
| **Lumi の機嫌** | **Sensor が止まっても知っている** | **Internal** |
| **Lumi の疲労** | 同上 | **Internal** |
| **現在の目標** | 同上 | **Internal** |
| **Drive 値** | 同上 | **Internal** |
| 時刻 | システムから取れなくなれば分からない | World |

### confidence の有無

World facet は「70% の確信でユーザーは在席している」がありうる（Sensor の精度）。

**Internal State に confidence は不要。** 「70% の確信で機嫌がいい」は意味をなさない。

### Mood が持続することが「生きている感じ」を作る

**AIRI では感情が LLM 出力から毎ターン導出される瞬間値でしかない**（`packages/stage-ui/src/stores/character/`）。持続的なムード状態も、慣性も、減衰も存在しない。

これが「生きている感じ」を最も損なっている。毎ターンリセットされる感情は、感情ではなく演出である。

Lumi の Mood は:

| 性質 | 内容 |
|---|---|
| **慣性** | 急には変わらない。1回の会話で機嫌が180度変わらない |
| **減衰** | 放っておくとニュートラルに戻る |
| **影響先** | プロンプト（口調）/ Drive（乗数）/ 表情（ベースライン） |

これは Internal State でしか表現できない。

### 混同したときに起きること

| 誤り | 結果 |
|---|---|
| World State を Memory に入れる | **ゴミ記憶が量産される**（「10:31 に Chrome が前面だった」を覚える意味はない） |
| Memory を状態管理に使う | 状態参照のたびに検索コストがかかる |
| Internal State を World facet にする | **mood が TTL で失効する** |

---

## Alternatives

### A. 単一の State ストア（当初案）

**利点:** ストアが1つ。実装が単純
**欠点:** TTL の扱いが項目ごとに違うことになる。`character.mood` に TTL を設定するか否かで必ず揉める

### B. World State に `ttl: null` を許す

**利点:** ストアが1つのまま、失効しない項目を表現できる
**欠点:** 「TTL が無い facet」と「Internal State」の違いが曖昧になる。confidence の扱いも揃わない。**型が同じなのに意味が違うものが混在する**

### C. Internal State を Memory に入れる

**利点:** 永続化の仕組みを共有できる
**欠点:** 記憶検索に mood がヒットする。減衰の意味が違う（記憶は忘れる、mood は中立に戻る）

### D. Mood を持たない（AIRI の実質的な状態）

**利点:** 実装コストゼロ
**欠点:** **「生きている感じ」が出ない。** 毎ターン LLM が決める感情は演出でしかない

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| ストアが2つ | World facet テーブルと Internal State テーブル |
| 分類の判断が要る | 新しい状態を追加するたびに「どっちか」を決める |
| プロンプトへの投影が2系統 | World の projection と Internal の記述 |

### 分類が迷う例と、その扱い

| 項目 | 分類 | 理由 |
|---|---|---|
| 最終対話からの経過時間 | **どちらでもない**（導出値） | `time.local` と Memory の最新 episode から計算する |
| ユーザーの好み | **Memory** | 観測ではなく、蓄積された信念 |
| 今話している話題 | **Working Memory** | セッション内。Internal でも World でもない |
| 未読の通知数 | World | 観測。Sensor が取る |

**「導出できるものはストアに置かない」**という原則を併せて適用する。

---

## Consequences

### Sensor は facet を直接書かない

```
Sensor Ext → Signal("sensor.foreground_app")
  → Core が認証・schema検証・capability検査
  → Core が WorldFacet を更新
  → Core が DomainEvent("WorldFacetChanged") を発行
```

Core が key の妥当性・型・TTL を決める（[ADR-010](ADR-010-signal-vs-domain-event.md) と整合）。

### 期限切れは `Unknown` として扱う

`None` を返すのではなく、**「知らない」を明示する**。プロンプトにも「分からない」と投影する。

```
「センパイが今何をしているかは分からないけど、
 21時だし、さっきまで Factorio を触っていたのは覚えてる」
```

**World（分からない）と Memory（覚えている）が自然に組み合わさる。**

### 表情が Mood と ACT の合成になる

```
Mood（Internal State。持続。慣性と減衰）    ← ベースライン
  +
<|ACT|> マーカー（瞬間値）                  ← この発話だけ
  =
最終的な ExpressionIntent
```

同じ「驚く」でも機嫌のいいときと悪いときで違って見える。

### Drive の抑制側が Internal State に属する

`fatigue` / `rest_pressure` は Drive の argmax 競争に参加せず、**全 Drive への乗数**として働く（[ADR-007](ADR-007-drive-system.md)）。

これらは観測ではないので Internal State に置く。

### プライバシー方針が World State 側に必要になる

`user.focus_app` はアプリ名を取るが、**ウィンドウタイトルは取らない**（機密情報が入りうる）。

タイトルが必要な機能を作る場合は、別の capability として明示的に宣言させ、ユーザー同意を必須にする。

**Internal State にはこの問題が無い**（Lumi 自身の状態しか入らないため）。分離により、プライバシー配慮の対象が World State に限定される。

### Inspector での表示が分かれる

| ストア | 表示 |
|---|---|
| World State | facet 一覧。**期限切れは灰色** |
| Internal State | mood / fatigue / arousal / drives の現在値と推移 |

分離により「何が観測で、何が Lumi の状態か」が UI 上でも明確になる。
