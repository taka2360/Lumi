# Memory Architecture

> **原則: 会話を無条件に記憶しない。記憶は後から作られる。**

親: [DESIGN.md](../DESIGN.md) / 関連: [../contracts/provenance.md](../contracts/provenance.md), [ADR-004](../decisions/ADR-004-sqlite-vec-memory.md), [ADR-013](../decisions/ADR-013-memory-assertion-mode.md)

---

## 1. 4層

| Layer | 所在 | 寿命 | 用途 |
|---|---|---|---|
| **Working Memory** | in-process | セッション | 今の話題、直近の意図、注意の焦点 |
| **Episodic Memory** | SQLite | 長期（減衰） | 「2026-08-10 に Factorio について会話した」 |
| **Semantic Memory** | SQLite + vec | 長期（信念） | 「ユーザーは Factorio が好き」 |
| **Procedural** | SQLite | 長期 | 学習した手順（Phase 後半） |

### World State / Internal State との区別

```
World State    外界の観測      安い / derived / 失効する
Internal State 自分の状態      安い / 蓄積される / 失効しない
Memory         覚えていること   高い / curated / 減衰する
```

**混同すると、World State がゴミ記憶を量産するか、Memory が状態管理に使われて破綻する。** → [world-state.md](world-state.md)

---

## 2. ストレージ

**SQLite + sqlite-vec + FTS5。単一ファイル、追加プロセスゼロ。** 〔Provisional〕

> **記憶 DB は保存時に暗号化する。** 保存先・鍵の扱い・保持期間・消去対象の唯一の定義場所は
> [../contracts/privacy.md](../contracts/privacy.md)（決定は [ADR-038](../decisions/ADR-038-privacy-and-data-retention.md)）。
> 要点のみ: ランダム鍵を Lumi が生成し、OS の秘密保管（Windows は DPAPI）に預ける。
> **ユーザーはパスワードを管理しない。** 同一ユーザー権限で動くソフトウェアからは守らない。
> **暗号化ビルドで sqlite-vec と FTS5 が使えることは確認済み**〔2026-08-22〕
> → [ADR-040](../decisions/ADR-040-encrypted-sqlite-driver.md) / [../measurements/phase2.md](../measurements/phase2.md)。

| 選択肢 | 評価 |
|---|---|
| **SQLite + sqlite-vec** ✓ | 単一ファイル。常駐プロセスもRAMも増えない。数十万ベクトルなら十分 |
| Qdrant | 常駐プロセス + RAM。単一ユーザーのデスクトップには過剰 |
| DuckDB WASM | AIRI の選択。ブラウザ前提。Python Core には不要な複雑さ |
| PostgreSQL + pgvector | サーバが要る。非目標 |

`VectorStore` インターフェースで隔離し、将来の差し替えを妨げない → [../interfaces/memory.md](../interfaces/memory.md)

### Embedding〔2026-08-22 確定 → [ADR-041](../decisions/ADR-041-embedding-model-harrier-oss.md)〕

**`microsoft/harrier-oss-v1-270m` を ONNX（q4）/ CPU 実行。次元 640。MIT。**
実行時取得（STT モデルと同じ仕組み。配布物に含めない）。

**CPU 実行が重要。** VRAM を LLM に全振りするため（[DESIGN.md](../DESIGN.md) の GPU 戦略）。

> **クエリと文書で入力が違う。** クエリにだけ指示文を付ける。
>
> ```text
> クエリ: "Instruct: {task}\nQuery: {text}"
> 文書:   "{subject}: {content}"      ← 指示文は付けない
> ```
>
> **付け忘れても例外は出ず、検索品質だけが静かに落ちる。**
> そのため `EmbeddingProvider` を `embed_query` / `embed_documents` に分け、型で強制する
> （[../interfaces/provider.md](../interfaces/provider.md)）。実測値 →
> [../measurements/phase2.md](../measurements/phase2.md)

### Episode と記憶レコードは別のもの〔2026-08-22 / Phase 2c 実装〕

**Episode は「実際に言われたこと」の生ログ**であり、記憶レコードは「そこから取り出した信念」である。
混ぜると、保持期間（§ [../contracts/privacy.md](../contracts/privacy.md) §4）が信念まで消しに来る。

| | 中身 | 寿命 |
|---|---|---|
| Episode / utterance | 発話そのもの（話者・本文・**trust_level**・時刻） | **既定 90 日で消える**（設定で `unlimited` も選べる） |
| 記憶レコード | そこから作られた信念 | 減衰して archive。**期限なし** |

**utterance には trust_level を一緒に保存する。** 後から計算し直すことはできず、
**いかなる自動処理も trust を上げてはならない**（Invariant 7）ため、記録が本文と一緒に旅する必要がある。

**1ストア1ファイル。** `memory.db` / `events.db` / `audit.db` に分かれている。
監査ログを「Tool 経路から到達不能」にする境界は、**ファイルが分かれていて初めて名指しできる**
（[../contracts/privacy.md](../contracts/privacy.md) §5）。

### マイグレーション

```sql
_schema_version (component, version, applied_at)
```

**ユーザーの PC に保存された記憶は、半年後の新バージョンでも読めなければならない。** 全テーブルにマイグレーション管理を適用する。
`component` はそのファイルが何の DB かを記録する。**別のスキーマで開こうとしたら、
テーブルを作り足すのではなく失敗する**（実装 2c）。

**ベクトル表と FTS5 は、埋め込みモデルを決めてから作る**（2e）。
`vec0` は作成時に次元を固定するため、先に作ると**測る前に次元を発明する**ことになる。
**次元は 640 で確定した**（[ADR-041](../decisions/ADR-041-embedding-model-harrier-oss.md)）。

---

## 3. MemoryCandidate — LLM の抽出結果をそのまま信用しない

### 問題

ユーザーが「冗談だけど、僕は火星人だよ」と言ったとき、LLM が素直に抽出するとこうなる。

```
user_species = "Martian"
confidence = 0.95
```

**LLM は「言われたこと」を抽出するのは得意だが、「それが本気か」の判定は当てにならない。**

### 解 — 認識論的メタデータ

`confidence`（確からしさ）とは**別軸**として `assertion_mode`（どういう根拠か）を必須にする。

```python
@dataclass
class MemoryCandidate:
    type: MemoryType             # episodic | semantic | procedural
    subject: str                 # 誰/何についての記憶か
    content: str

    assertion_mode: AssertionMode     # 型定義 → interfaces/memory.md
    evidence_ref: list[UtteranceId]   # 根拠となる発話への参照
    confidence: float                 # 0.0-1.0

    provenance_class: ProvenanceClass
    trust_level: TrustLevel

    salience: float
    valid_from: datetime
    superseded_by: MemoryId | None
    source_episode_ids: list[EpisodeId]
```

**`AssertionMode` / `MemoryRecord` の型定義は [../interfaces/memory.md](../interfaces/memory.md) が唯一の定義場所。** ここでは規則と根拠を述べる。

| assertion_mode | 意味 |
|---|---|
| `user_confirmed` | ユーザーが記憶 UI で確認・訂正した（最強） |
| `user_stated` | ユーザーが明示的に述べた |
| `inferred` | 会話から推測した |
| `self_generated` | Lumi 自身の推測・想像 |
| `external` | 外部データ由来 |

### 規則

| # | 規則 |
|---|---|
| 1 | `self_generated` / `inferred` は、プロンプトに入れる際に**「Lumi がそう思っているだけ」と明示する**。事実として提示しない |
| 2 | `user_stated` でも、**冗談・仮定・引用の文脈は `confidence` を下げる**。LLM 抽出時にこれを明示的に問う |
| 3 | `user_confirmed` への昇格は、ユーザーが記憶 UI で確認した時のみ |
| 4 | **`user_confirmed` は `trust_level` が `trusted` に昇格する唯一の経路**（Invariant 7） |
| 5 | 矛盾検出時は `assertion_mode` の強い方を優先 |
| 6 | **`MemoryCandidate` の `provenance_class` / `trust_level` に既定値を置かない**〔2d〕。既定を置くと、書き忘れた候補が黙って trusted で入る |

**`trust_level = TRUSTED` を渡す場所は静的検査で列挙する**（`core/tests/test_kernel_boundaries.py`）。
検査は AST で見る。`trust_level=` という書き方を文字列で探す実装では、
**`confirm()` が SQL のパラメータとして渡している昇格を取りこぼす**。

### プロンプトへの反映

| assertion_mode | 提示 |
|---|---|
| `user_confirmed` / `user_stated` | 事実として提示 |
| `inferred` | 「〜と思われる（会話から推測）」 |
| `self_generated` | 「〜とわたしは思っている（根拠は自分の推測）」 |
| `external` | 出所を明記し、untrusted ブロックに入れる |

### なぜこれが人格設計でもあるのか

**「わたしがそう思ってるだけかもだけど」と言えるAIと、幻覚を事実として断言するAIの差はここで決まる。**

これはセキュリティ（Invariant 3, 7）であると同時に、Lumi が「生き物っぽい」と感じられるかどうかの分かれ目でもある。自分の知識の出所と確からしさを区別できることは、人格の一部である。

---

## 4. 形成 — Reflection Job

**会話中には記憶を作らない。** バックグラウンドで、後から作る。

### Reflection は Activity ではなく Job

**`Job(kind=reflection, actor=system, uses_inference=True)`** として実行する（[ADR-018](../decisions/ADR-018-foreground-and-jobs.md)）。

| 性質 | 内容 |
|---|---|
| foreground | **取らない。** 会話と同時に進んでよい処理だから |
| `actor` | **`system` 固定 → L0 のみ。** 記憶の書き込みは `memory` lane の L0 |
| 推論 | **`arbiter.inference_lease()` を取る**（下記） |
| cancellation | `cooperative` |

### 推論資源の調停が必須

Reflection は LLM を呼ぶ。**これを Arbiter の管理外で走らせると barge-in が破れる。**

```
Reflection Job が LLM で記憶を抽出中（GPU 占有）
  → ユーザーが話しかける
  → 会話の LLM 初トークンが Reflection の完了待ちになる
  → p95 2.0 秒 の SLO を直撃する
```

```python
async with arbiter.inference_lease(job) as lease:
    candidates = await llm.extract(episodes, cancel_token=lease.token)
    # foreground が推論を要求すると lease.token が fire する
```

**revoke されたら進捗を捨てて中断し、次の起動タイミングでやり直す。** Reflection は急がない処理なので、部分結果を保存する複雑さを持ち込まない。

### 起動タイミング

- セッション終了時
- 長いアイドル時（ユーザーが席を外している間）
- 明示的な要求（「今の覚えておいて」）

**いずれの場合も foreground を奪わない。** 「今の覚えておいて」への返事は会話 Activity が即座に行い、実際の抽出は Job が後から行う。

### 実装〔2026-08-23 / 2f〕

`core/lumi/memory/reflection.py`。**起動条件は「アイドルが続いたこと」1つに集約した。**

| 設計上の起動タイミング | 実装 |
|---|---|
| 長いアイドル時 | **これを実装した**（既定 5 分。60 秒ごとに確認） |
| セッション終了時 | **次回起動のアイドル判定が拾う。** 終了時に推論を待たせない——終了が遅いアプリになる |
| 明示的な要求 | 2g の記憶 UI と同じ画面に属する（未実装） |

- **どこまで抽出したかは `episodes.reflected_turns` で持つ。** Episode はセッション中ずっと開いて
  いるので、「この Episode は済んだか」では毎回全部読み直すか一度きりで止まるかにしかならない
- **watermark は書き込みが終わってから動かす。** 先に動かすと、落ちたときに記憶が失われた事実まで消える
- **revoke されたら watermark は動かさない**（[ADR-018](../decisions/ADR-018-foreground-and-jobs.md)）。
  同じ発話を次回また読む。部分結果は保存しない
- **LLM が自然終了しなかった場合も watermark は動かさない。** 結果全体を捨てて次回やり直す

##### 生成の終わり方と、watermark をどうするか

**ここが単一定義である**（実装は `memory/reflection.py` の `_Ask`）。
「パースできたか」ではなく**終了理由**で決める。切れた JSON 配列は短い配列として素直にパースできる。

| 終わり方 | このパス内で引き直すか | watermark | 結果を書くか | `interrupted` | `rejected` |
|---|---|---|---|---|---|
| `Finish("stop")` | — | **進む**（読んだバッチ分） | 書く | false | 項目ごとの理由のみ |
| `Finish("length")`・バッチ > 1 | **引く**（バッチを半分に） | 成功した試行が出るまで進めない | 書かない | false | — |
| `Finish("length")`・バッチ = 1・上限内 | **引く**（同じバッチで再サンプル） | 進めない | 書かない | false | — |
| `Finish("length")`・バッチ = 1・上限到達 | 引かない | **進む**（その1発話を飛ばす） | 書かない | false | **残す** |
| 未知の `Finish.reason` | 引かない | **動かさない** | 書かない | true | — |
| `Finish` が来ずにストリーム終了 | 引かない | **動かさない** | 書かない | true | — |
| `LLMFailure` / 例外 | 引かない | **動かさない** | 書かない | true | — |
| 生成前の revoke | 引かない | **動かさない** | 書かない | true | — |
| 生成中の revoke | 引かない | **動かさない** | 書かない | true | — |
| 切断後・次の試行の前の revoke | 引かない | **動かさない** | 書かない | true | — |

- **`length` だけは「次回やり直す」では終わらない。** トークン上限による切断は、
  **同じ発話列を同じ上限で読む限り次回も同じ場所で起きる**。`unreflected()` は古い順に返すので、
  そのままにすると**その Episode 以降が永久に詰まり**、アイドルのたびに推論を無駄に焼く。
  したがって **その場でバッチを半分にして引き直す**
- **1発話まで縮めてもなお `length` の場合、すぐには諦めない。**
  抽出は temperature 0.2・**seed 無し**（製品経路では設定しない）で走る確率的な生成であり、
  1回の `length` は「この発話は抽出不能」の証拠ではなく**引きの悪さ**でありうる。
  **追加2回まで同じバッチで引き直す**（`FLOOR_TRUNCATION_RETRIES`。合計3サンプル）。
  縮める余地が無い場所なので、変えられるのはサンプルだけである
- **上限まで全部 `length` なら、その1発話を諦めて watermark を進める**
  （`rejected` に理由を残し、`reflection.truncated_at_floor` を error で出す）。
  1発話ぶんの取りこぼしは損失だが、**二度と流れない抽出キューは機能の停止である**
- ★ **未知の `Finish.reason` は「詰まる」側に倒す。** 誰も読んだことのない理由は
  「答えが完全である」とは仮定できず、**サーバ側で打ち切られた場合と区別もできない**ので、
  転写を残してキューを止める。この停止は**見える**（記憶ウィンドウが `unreflected_turns()` を出す）し
  error ログにも出る。理解していない理由で watermark を動かすほうは、静かで取り返しがつかない。
  なお Provider 側では **`done_reason: null` を「理由の不在」として `stop` に正規化する** ——
  JSON の null ひとつで抽出キューが止まるのは、この fail-closed の意図ではない
- **どの試行を始める前にも、その都度 revoke を確認する。** Provider は
  `/api/chat` を投げてからトークンを見るので、確認が「答えが返った後」だけだと
  **revoke 済みの2回目の生成がユーザーのターンと競合する**。確認は `_ask` の直前に置く。
  半分に縮めた再試行も、同じバッチでの再サンプルも、等しく2回目の背景生成である
- **revoke は切断に勝つ。** 再サンプル中に revoke されたら watermark は動かさない——
  「上限まで `length` だったから諦める」という判定も、revoke 後のこのパスが下してよい判定ではない
- **1回のパスが失敗しても scheduler のループは死なない。** `ReflectionScheduler` は起動時に
  一度だけ spawn される。例外がループの外に出れば**その起動の間ずっと記憶が作られなくなる**——
  バッチ縮小が防いでいるのと同じ「止まったキュー」に、外側から到達してしまう
- ★ **途中で落ちたパスも「何を書いたか」は返す。** レポートは**書き込みが起きた唯一の記録**であり、
  scheduler はその `learned` を見て埋め込み（2e の `Indexer`）と記憶ウィンドウへの通知を判断する。
  鋭いのは `mark_reflected()` が失敗した場合で、**記憶は commit 済み・watermark は未更新**になる。
  ここで例外がレポートを置き換えると、次のパスは同じ発話を読み直して**自分の書き込みを
  `DUPLICATE` と判定する**（`learned` は 0）。結果、その記憶は**再起動まで埋め込まれないまま**——
  キーワードでは引けるが類似検索からは見えない状態で残る。
  したがって例外は episode ループの中で受け、`interrupted` として**カウントごと**返す。
  **同じ規則は1段内側にも適用する**——1つの回答から2件書く途中で `reconcile` が落ちた場合も、
  書けた分を数えたまま `interrupted` で止める（watermark を進めれば残りが永久に飛ぶ）。
  なおこれは**その項目の `rejected` ではない。** `rejected` は「この抽出が駄目だった」であって、
  書けなかった原因が store 側にある場合の理由ではない
- **抽出した記憶は同じアイドル時間にインデックスする**（2e の `Indexer`）
- **`novelty` は subject で測る**（既知の subject なら 0.0、初めてなら 1.0）。
  埋め込み距離のほうが細かいが、**候補が埋め込まれるのは書き込みの後**（`Indexer`）なので
  ここでは取れない。粗さは自覚している——既知の subject に付く**無関係な2つ目の事実が
  「よく知っている」側に倒れる**

#### プロンプトは実測で2回壊れた

**「転写の後ろに問いを置かないと `[]` しか返さない」**、
**「subject の規則が弱いと全部が同じ subject になる」**——後者は
`reconcile` が矛盾と見なすため、**無関係な事実が別の事実を supersede して消える**。
→ [../measurements/phase2.md](../measurements/phase2.md)

### 流れ

```
recent_episodes
  ↓ LLM 抽出（プロンプトは外出し。Provisional）
MemoryCandidate[]
  ↓ 重複統合（既存記憶との類似度）
  ↓ 矛盾検出（同一 subject の既存 semantic memory と比較）
  ↓ assertion_mode の検証（evidence_ref が実在するか）
  ↓ provenance 付与（元 episode の trust_level から join）
  ↓ salience の決定論的補正
書き込み
```

### salience の決定論的補正

**LLM の主観だけに任せない。**

```python
salience = clamp(
    llm_salience * 0.4
    + emotional_intensity * 0.2      # 会話の感情強度（TTS/表情マーカーから）
    + novelty * 0.15                 # 既存記憶との距離
    + explicit_marking * 0.15        # 「覚えておいて」と言われたか
    + repetition * 0.10              # 何回言及されたか
)
```

さらに事後的に `access_boost`（検索でヒットした回数）が加算される。

**実装は `core/lumi/memory/decay.py` の純粋関数**〔2026-08-22 / 2d〕。
LLM が出す `llm_salience` / `emotional_intensity` / `novelty` は **範囲外の値が来る前提で丸める**
（捨てると「抽出できたのに記録が落ちる」になる）。`repetition` は上限で頭打ちにする——
同じ話を 50 回したことと 5 回したことの差を、他の項を潰すほど大きく扱わない。

---

## 5. 忘却

```python
def effective_salience(m: MemoryRecord, now: datetime) -> float:
    dt = (now - m.last_accessed).total_seconds()
    tau = TAU[m.type]                    # episodic は短く、semantic は長い
    return m.base_salience * exp(-dt / tau) + access_boost(m.access_count)
```

`floor` を下回ったら `archived_at` を立てる。

### 物理削除しない

> **「思い出せなくなる」であって「無かったことになる」ではない。**

- archived な記憶は通常の検索にヒットしない
- ユーザーが記憶 UI で明示的に削除したときのみ物理削除する
- **記憶レコードは保持期間の対象ではない**（Episode の生ログとは別扱い → [../contracts/privacy.md](../contracts/privacy.md) §4）
- 「あれ、なんだっけ…」と言ったあとで、関連する強い手がかりがあれば思い出せる余地を残す

### 物理削除は `MemoryStore` に無い〔2026-08-22 / 2d〕

**`purge()` の実装は `core/lumi/storage/retention.py` に置く。**
ユーザーデータを消す `DELETE` を1ファイルに閉じるという境界（[../contracts/privacy.md](../contracts/privacy.md) §5）は、
**記憶 UI の削除だけが別の場所にあった時点で検査できなくなる**。
記憶ストア側には削除経路が無く、`archive()`（`UPDATE`）だけがある。

**根拠（`evidence_ref` / `source_episode_ids`）は外部キーにしない。**
Episode は既定 90 日で消えるが記憶は残るため、外部キーにすると
「Episode を消せない」か「Episode と一緒に信念が消える」のどちらかになる。
**参照が切れることは想定内**であり、切れても記憶は読める。

### τ と floor〔Provisional。2026-08-22 に具体値を置いた〕

| type | τ | floor を割るまで（無参照・base 1.0） | 意味 |
|---|---|---|---|
| episodic | **14 日** | 約 42 日 | 「先週こんな話をした」は薄れる |
| semantic | **180 日** | 約 1.5 年 | 「Factorio が好き」は長く残る |
| procedural | **730 日** | 約 6 年 | 手順は忘れにくい |

`floor = 0.05` / `access_boost` は上限 0.2。**数値は目安であり、Phase 2 で使いながら調整する。**
表を動かすときは `core/lumi/memory/decay.py` の定数も同じ変更で動かす。

---

## 6. 矛盾 — バージョン付き信念

**Semantic memory は上書きしない。supersede する。**

```
memories:
  id=1  subject="user.hobby"  content="Factorio が好き"
        valid_from=2026-03-01  superseded_by=2
  id=2  subject="user.hobby"  content="最近は Rimworld をやっている"
        valid_from=2026-08-01  superseded_by=NULL
```

### これができるようになること

```
Lumi「前は Factorio 好きって言ってたけど、最近はどう?」
```

**時間を持った会話**が可能になる。これは体験上とても大きい。単に最新の値を持つだけでは、Lumi は「ずっと今の状態しか知らない存在」になる。

### 矛盾検出時の処理

```
新しい MemoryCandidate と既存の semantic memory が同一 subject で矛盾
  ↓
assertion_mode を比較
  user_confirmed > user_stated > inferred > self_generated > external
  ↓
新しい方が強いか同等 → 既存を supersede
新しい方が弱い       → 新しい方を confidence 低で保存（または破棄）
  ↓
どちらの場合も、矛盾があったこと自体を episodic に記録
```

最後の1行が重要。**Lumi が「あれ、前と言ってること違うね」と気づける**ようにする。

### 実装〔2026-08-22 / 2d〕

- 判定は `core/lumi/memory/contradiction.py` の**純粋関数**。**LLM に決めさせない**
- **矛盾の episodic 記録は `supersede()` と同じトランザクションで書く。**
  呼び出し側の作法にすると、いつか忘れられて「静かに上書きされたのと同じ」状態になる
- その記録の `assertion_mode` は **`inferred`**。ユーザーが「前は違うことを言った」と述べたわけではなく、
  Lumi が自分の記録2件から気づいた事実だから（プロンプトでは「〜と思われる」として出る）
- 同一 subject かどうかで探す。**内容の意味的な近さは 2e**（埋め込みが無いうちは、
  近いと**判定できない**のであって、判定を省いているのではない）
- **矛盾するのは semantic 同士だけ。** 候補が episodic / procedural なら比較に入れない——
  「月曜に Factorio の話をした」が「ユーザーは Factorio が好き」を supersede してしまい、
  **出来事が信念を静かに置き換える**（重複の統合は type ごとに行うので、こちらは全 type 対象）

---

## 7. 検索

**ハイブリッド + 固定トークン予算。**

```python
candidates = union(
    vector_search(query_embedding, k=K1),      # 意味的類似
    fts_search(query_keywords, k=K2),          # キーワード一致
    recent(k=K3),                              # 最近のもの
)

scored = [
    (m, (w1*similarity + w2*recency + w3*effective_salience) * assertion_weight(m))
    for m in candidates
]

selected = pack_into_budget(sorted(scored, reverse=True), token_budget)
```

**`assertion_weight` は加算ではなく乗算。** 重みが 1.2 / 1.0 / 0.8 / 0.6 / 0.7 という「倍率」として定義されているため。加算にすると、類似度が 0 の記憶でも assertion だけで浮上してしまう。

### 予算超過時の切り落としも決定論的に

**LLM に「適当に切って」をさせない。** スコア順に詰めて、入らないものを落とす。落としたことはログに残す（Inspector で見える）。

### assertion_weight

| assertion_mode | 重み |
|---|---|
| `user_confirmed` | 1.2 |
| `user_stated` | 1.0 |
| `inferred` | 0.8 |
| `self_generated` | 0.6 |
| `external` | 0.7 |

〔Provisional。Phase 2 で調整〕

### 実装〔2026-08-22 / 2e〕

`core/lumi/memory/retrieval.py`。**式・重み・切り落としはすべて純粋関数**で、
`ScoreBreakdown` が全項を返す（Inspector で「なぜ選ばれた / 選ばれなかった」が見えるため）。

| | 値〔Provisional〕 |
|---|---|
| 重み | similarity 0.6 / recency 0.15 / effective_salience 0.25 |
| recency の τ | **30 日**。減衰（忘却）とは別物で、こちらは**話題としての近さ**を測る |
| 候補数 | vector 8 / keyword 8 / recent 4 |
| 記憶のプロンプト予算 | 400 トークン（`PROMPT_BUDGET_TOKENS` 3000 の一部） |

- **recency は `valid_from` から測る。** 去年の話を今日書き留めた記憶は「新しい」ではない
- **`recent` を候補に混ぜる。** 書かれたばかりの記憶はまだ埋め込みが無く、
  これが無いと**次のインデックス作成まで見えない**——ユーザーが最も参照しそうな瞬間に
- **キーワード検索は3文字未満を送らない**（FTS5 trigram の原理的限界 →
  [../measurements/phase2.md](../measurements/phase2.md)）
- **ユーザーの発話は FTS5 のクエリ構文ではない。** 引用して渡す（Invariant 3 の小さい版）

---

## 8. 記憶 UI（Phase 2 で必須）

**ユーザーが記憶を見て直せることは必須機能である。**

| 機能 | 理由 |
|---|---|
| 一覧・検索 | 何を覚えられているか知る権利 |
| 編集 | 誤った記憶を直せないと、間違いが永久に残る |
| 削除（物理） | 忘れてほしいことを忘れさせる |
| **確認（`user_confirmed` への昇格）** | **`tainted → trusted` の唯一の昇格経路**（Invariant 7） |
| supersede 履歴の閲覧 | 「前はこう言ってたよね」の根拠を見る |

### 実装〔2026-08-23 / 2g〕

**独立ウィンドウ（`memory`）。** 画面と操作の一覧は
[ui.md](ui.md) §5b が唯一の定義場所。ここに書くのは記憶側の規則だけ。

| 操作 | 記憶側で起きること |
|---|---|
| 編集 | **上書きしない。** `supersede` して新しいレコードを立て、直した事実を履歴に残す |
| 編集した記憶の `assertion_mode` | **`user_confirmed`。** ユーザーが自分で書いた文がそれ未満になる理由がない（[ADR-043](../decisions/ADR-043-user-edited-memories-are-confirmed.md)） |
| 編集できるもの | **live な belief だけ。** supersede 済みの行を直すと後継が2つできる |
| 確認 | `MemoryStore.confirm()`。**`tainted → trusted` の唯一の昇格経路**（Invariant 7） |
| 削除 | archive ではなく**物理削除**。経路は `retention.py` の1箇所（[../contracts/privacy.md](../contracts/privacy.md) §5） |
| エクスポート | **平文の JSON。** 書き出す前に平文であることを言う |

**編集と確認だけが `user_confirmed` を書ける。** Reflection は書けない
（`EXTRACTABLE` に含まれない）。**ユーザーが押したことだけが根拠になる。**

---

## 9. AIRI との比較

**AIRI の記憶は実質「会話履歴の全文をプロンプトに毎回積む」だけ。**

| | AIRI | Lumi |
|---|---|---|
| 長期記憶 | `memory_fragments` / `memory_episodic` / `memory_long_term_goals` はスキーマ定義のみ、**参照コードがゼロ**。`memory-pgvector` は27行のスタブ | 4層 + Reflection Job |
| 忘却 | `importance` / `last_accessed` / `access_count` 列は**一度も読み書きされない** | 指数減衰 + archive |
| 矛盾 | なし | supersede + valid_from |
| 検索 | stage 側に埋め込みも意味検索も**存在しない**（telegram-bot のみ pgvector を使用） | ハイブリッド + 予算 |
| 圧縮 | `compactConversationEntries()` は実装済みだが**未 export・未配線の死にコード** | 予算固定 + 決定論的切り落とし |
| 認識論 | なし | assertion_mode + evidence + provenance |
| 記憶 UI | なし | Phase 2 で必須 |

AIRI は「設計だけが SQL スキーマとして残った未実装機能」の状態にある。スキーマ自体（`memory_type`, `importance`, `emotional_impact`, `memory_episodic`, `memory_long_term_goals`）は方向性として妥当だが、**実装されていないため設計の妥当性は検証されていない**。Lumi は独自に設計する。

---

## 10. テスト

**これらは LLM を呼ばずにテストできなければならない。**

| # | テスト |
|---|---|
| 1 | `effective_salience` の減衰が時間経過に対して期待通り |
| 2 | floor を下回った記憶が archive され、通常検索にヒットしない |
| 3 | archive された記憶が物理削除されていない |
| 4 | supersede が既存を上書きせず履歴を残す |
| 5 | 矛盾検出が `assertion_mode` の強弱を正しく比較する |
| 6 | 矛盾があったこと自体が episodic に記録される |
| 7 | 検索が予算超過時に決定論的に切り落とす |
| 8 | provenance が Reflection Job で正しく伝播する（元が untrusted なら derived） |
| 9 | **`user_confirmed` 以外の経路で `trust_level = trusted` にならない**（Invariant 7） |
| 10 | マイグレーションが旧バージョンの DB を正しく読める |
| 11 | salience の決定論的補正が入力に対して期待通り |
| 12 | LLM 抽出部分はプロンプト構築のスナップショットテスト（入力の正しさを検証） |
