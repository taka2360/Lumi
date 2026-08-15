---
paths:
  - "core/lumi/memory/**/*.py"
  - "core/lumi/storage/**/*.py"
  - "core/lumi/world/**/*.py"
  - "core/lumi/internal/**/*.py"
---

# Memory / World State / Internal State

設計 → [architecture/memory.md](../../docs/architecture/memory.md), [interfaces/memory.md](../../docs/interfaces/memory.md), [architecture/world-state.md](../../docs/architecture/world-state.md)

## 三分離を混同しない

```
World State    外界の観測      安い / derived / TTL で失効する
Internal State 自分の状態      安い / 蓄積される / 失効しない
Memory         覚えていること   高い / curated / 減衰する
```

- World State を Memory に入れない → ゴミ記憶が量産される（「10:31 に Chrome が前面だった」を覚える意味はない）
- Memory を状態管理に使わない → 検索コストが状態参照のたびにかかる
- Internal State を World facet にしない → mood が TTL で失効して「機嫌が分からない」になる

判定: **「観測できなくなったら『分からない』になるか?」** → Yes なら World

## Memory の原則

| # | 原則 |
|---|---|
| 1 | **会話中に記憶を作らない。** Reflection Job がバックグラウンドで後から作る |
| 2 | **上書きしない。** `supersede`（`valid_from` / `superseded_by`）で履歴を残す |
| 3 | **物理削除しない。** `archive` する。`purge()` はユーザーの明示操作でのみ |
| 4 | **LLM の抽出結果をそのまま信用しない。** `assertion_mode` を必須にし、salience は決定論的に補正する |
| 5 | **`confirm()` が `trust_level = TRUSTED` への唯一の昇格経路**（Invariant 7） |
| 6 | 矛盾を検出したら、**矛盾があったこと自体を episodic に記録する**（Lumi が「前と言ってること違うね」と気づけるように） |

`confidence`（確からしさ）と `assertion_mode`（どういう根拠か）は**別軸**。
「強く確信しているが、それは自分の推測」がありうる。

## Reflection は Job

`Job(kind=reflection, actor=system, uses_inference=True)`。**Activity ではない。**

```python
async with arbiter.inference_lease(job) as lease:
    candidates = await llm.extract(episodes, cancel_token=lease.token)
```

revoke されたら**進捗を捨てて中断**し、次の起動タイミングでやり直す。部分結果を保存する複雑さを持ち込まない。

## 検索

- ハイブリッド（vector + FTS5 + recency + salience）× `assertion_weight`（**乗算**。加算ではない）
- **予算超過時の切り落としは決定論的。** LLM に「適当に切って」をさせない
- `dropped` と `breakdown` を返す。**「なぜこの記憶が使われた / 使われなかったか」が Inspector で見えないと Phase 6 でチューニング不能になる**

## World State

- **Core は OS をポーリングしない。** Sensor Extension が Signal を push し、**Core が facet を書く**
- 期限切れ facet は `None` ではなく **`Unknown`**。プロンプトにも「分からない」と投影する
- プロンプトには生の facet 列ではなく**圧縮した projection** を入れる
- **`user.focus_app` は取るが、ウィンドウタイトルは取らない**（機密情報が入りうる）

## ストレージ

- **全テーブルにマイグレーション管理を適用する**（`_schema_version`）。ユーザーの PC の記憶は半年後の新バージョンでも読めなければならない
- マイグレーション失敗時は**旧 DB をバックアップして中断**。データを失わない
- 埋め込みモデルを変えると既存ベクトルが無効になる。`embedding_model_id` の不一致を検出する
- `VectorStore` interface 越しに使う（sqlite-vec の選択を可逆に保つ）

## Phase 2 着手前

**プライバシー方針（`docs/contracts/privacy.md`）が未策定なら、先にそれを書く。**
生ログの保持期間・暗号化・アンインストール時の挙動・第三者の音声を決めずにスキーマを作ると、作り直しになる。
