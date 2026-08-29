# ADR-045: Core のモジュール階層を静的検査で固定する

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-08-29 |
| **関連** | [architecture/core.md](../architecture/core.md) §4, [contracts/authority-matrix.md](../contracts/authority-matrix.md), [contracts/invariants.md](../contracts/invariants.md) Invariant 7, [ADR-023](ADR-023-llm-runtime-and-model-acquisition.md), [ADR-041](ADR-041-embedding-model-harrier-oss.md), [ADR-043](ADR-043-user-edited-memories-are-confirmed.md) |

## Decision

**Core のモジュール階層について3つを決め、いずれも AST の静的検査で固定する。**

### 1. モデル / エンジンの pin 情報は `lumi/artifacts/` に置き、依存は `artifacts ← providers ← setup` の一方向とする

`setup/models.py` / `setup/engines.py` / `setup/install.py` を `lumi/artifacts/` に移す。

**`providers/**` が `lumi.setup` を import することを禁止する。** 逆（`setup → providers`）は許す。

### 2. 「何を抽出するか」は `memory/`、「いつ走らせるか」は `agent/`

Reflection の起動条件（アイドル判定・明示要求の消費・inference lease の取得）は
`agent/reflection_scheduler.py` に置く。`memory/reflection.py` は
「転写を受け取って候補を返す」ことだけを持つ。

これは新しい規則ではなく、[core.md](../architecture/core.md) §4 の
**「`Memory` → `Agent` の依存を作らない」の適用**である。

### 3. 記憶レコードへの write は `MemoryStore` だけが行う

`memories` テーブルへの `INSERT` / `UPDATE` / `DELETE` が `memory/store.py` 以外に
存在しないことを静的検査で表明する（保持期間ジョブと全消去サービスは
[privacy.md](../contracts/privacy.md) §5 の別経路で、`storage/retention.py` が持つ）。

## Reason

### 1 — 循環は「たまたま壊れていない」状態でしか止まっていなかった

実装時点で次の相互依存があった。

```
providers/embedding/harrier.py:54-55     → lumi.setup.install, lumi.setup.models
providers/stt/faster_whisper.py:45-46    → lumi.setup.install, lumi.setup.models
providers/tts/engine_process.py:23       → lumi.setup.state.EngineRuntime
providers/tts/provider.py:33             → lumi.setup.state.EngineRuntime
        ↕
setup/detect.py:20                       → lumi.providers.llm.ollama (DEFAULT_PORT, HOST)
setup/ollama.py:17                       → lumi.providers.llm.ollama (DEFAULT_PORT, HOST)
```

**package として循環しているが、具体モジュール同士がたまたま噛み合っていないので
ImportError にならない。** `providers/llm/ollama.py` が `setup/models.py` を
import する日が来た瞬間に、起動しなくなる。**その日は import を1行足した人には見えない。**

置き場所が間違っているのは pin 情報のほうである。`STT_MODELS` や `model_directory()` が
答えるのは「どのモデルをどこに置くか」で、**Provider（実行時に読む側）と setup（取得する側）の
両方が必要とする**。片方の下に置けば、もう片方が必ず逆向きに手を伸ばす。

`EngineRuntime`（`setup/state.py:39-57`）も同じ理由で置き場所が違う。これは
**エンジンプロセスの状態**であって、セットアップの状態ではない。TTS provider が
setup に依存する理由はこれ1つしかなかった。

### 2 — スケジューリングを `memory/` に置くと、禁止済みの依存が復活する

Reflection の起動判断が実際に触るものを数えると:

| 触るもの | どこの概念か |
|---|---|
| `ReactiveLoop.idle_for()` / `take_remember_request()` | agent |
| `AttentionArbiter`（inference lease を取る） | kernel（agent が持つ） |
| `ProviderRegistry` / `ProviderKind.LLM` | providers（agent が配線する） |
| `server.notify(Role.PANEL, ...)` | transport |

**どれも memory の概念ではない。** これを `memory/scheduler.py` に置けば
`memory → agent` の依存が生まれ、§4 の表が禁じているものが復活する。

禁止の理由は §4 に書いてある通り「**記憶はエージェントの都合を知らない**」。
Phase 3 で Drive / AutonomyGate が「いつ喋るか」を決めるようになったとき、
**「いつ思い出すか」がそれと同じ場所にないと、2つの起動判断が別々に鬱陶しくなる。**

### 3 — 書き込み authority は「ファイルの大きさ」より優先される

`MemoryStore` は 769 行あり、分割したくなる。しかし
`mark_embedded()` を「インデクサ専用だから」と外に出すと:

```python
def _mark_blocking(self, memory_ids, model_id) -> None:      # store.py:410-416
    with self._db.transaction() as conn:
        for memory_id in memory_ids:
            conn.execute("UPDATE memories SET embedding_model_id = ? WHERE id = ?", ...)
```

**これは `memories` への write である。** 出した瞬間に「記憶レコードの唯一の書き手」が
2つになる。次に誰かが「ここでも1列だけ更新したい」と考えたとき、**前例ができている。**

信頼レベルの昇格が1箇所であること（[ADR-043](ADR-043-user-edited-memories-are-confirmed.md) /
Invariant 7）は、**書き手が1つであることに乗っている**。書き手が散れば、
`_confirm_in()` を1箇所に保つ検査は「その1箇所を通らない経路」を見逃す。

**行数を減らすために authority を割るのは、割に合わない取引である。**

## Alternatives

### 1 について

**`setup/` から `artifacts` を再エクスポートして、既存の import 文を変えない。**
利点: 差分が小さい。採らない理由: **定義場所が2つに見える**（[DESIGN.md](../DESIGN.md) §12 の
SSoT 規則違反）。しかも `providers → setup` の import 行が残るので、
静的検査は「再エクスポートだから可」という例外を持つことになり、**例外のある検査は守られない。**

**`providers/` の側に pin を持たせ、`setup/` がそれを読む。**
利点: 新しい package が要らない。採らない理由: **Provider を1つ足すたびに、
まだ何もダウンロードしていない段階で Provider モジュール（httpx / onnxruntime を引く）が
import される。** セットアップ画面を出すのに推論ランタイムを読み込むことになる。

**循環を許し、`import` を関数内に移して遅延させる。**
利点: 今日は動く。採らない理由: **これは循環を隠す手であって、直す手ではない。**
依存の向きが読めなくなるぶん、状況は悪化する。

### 2 について

**`ReflectionScheduler` を `memory/` に置き、必要なものを Protocol で注入する。**
利点: 「記憶のことは memory/ にある」という素朴な期待に合う。採らない理由: 注入で消えるのは
**import の矢印だけで、概念の依存は消えない**。`memory/` の中に
「アイドルとは何秒か」「inference lease とは何か」が書かれることになる。

### 3 について

**`EmbeddingQueue` を作り、`needing_embedding` と `mark_embedded` の両方を持たせる。**
利点: `MemoryStore` の public メソッドが 17 → 15 に減る。採らない理由: 上記の通り
`mark_embedded` は write である。**読み書きの境界ではなく、呼び出し元で切ってしまっている。**

**`MemoryStore` を write 専用にし、read を全部外に出す。**
利点: authority が最も明確になる。採らない理由: `reconcile()` は
**書く前に既存の live な belief を読む**。読み書きを完全に分けると、
矛盾解決が2オブジェクトにまたがったトランザクションになる。

## Trade-offs

**受け入れるコスト**

- `lumi/artifacts/` という package が増える。トップレベルの数が 13 → 14 になる
- `providers/tts/*` と `setup/state.py` の import 行が変わる。wire の値は変わらないが、
  **Python 側の定義位置が変わるので、`EngineRuntime` を探す人の一次記憶とズレる期間がある**
- 静的検査が 3 本増え、CI の実行時間がわずかに伸びる
- `MemoryStore` は 480 行程度までしか縮まない（write を出さないため）

**得るもの**

- **循環が「起きてから気づく」ものではなくなる。** 今回の循環は
  [authority-matrix.md](../contracts/authority-matrix.md) の19項目にこの検査が無かったから生まれた
- セットアップ画面を出すのに推論ランタイムを import しなくてよくなる
- Phase 3 で「いつ喋るか」と「いつ思い出すか」が同じ層に並ぶ
- Invariant 7 の昇格検査が、書き手が1つであることに引き続き乗れる

## Consequences

- [architecture/core.md](../architecture/core.md) §4 のモジュール構成を、**実装に合わせて書き直す**。
  併せて `world/` `internal/` `extensions/` `providers/vision/` に Phase 注記を付け、
  **将来形と現在形を区別する**（現状これらは存在しないのにツリーに載っており、
  逆に実装済みの `setup/` `panel/` が載っていない）
- [contracts/authority-matrix.md](../contracts/authority-matrix.md) の静的検査に3行を追加する
  （package 循環 / `providers → setup` の禁止 / `memories` への write 箇所）
- `core/tests/test_kernel_boundaries.py` に上記3本を実装する。
  **既存の #1/#2/#3/#7/#8/#9/#11/#12/#15 と同じファイルに置く**——
  「境界はここで守られている」を1ファイルに集める
- `CLAUDE.md` の「静的検査 — 未実装」の記述を、実装済み項目を数えた形に直す
- **これ以降 Core のモジュールを分割するときは、§4 のツリーを同じコミットで更新する。**
  コードだけ動かして §4 を置き去りにしたことが、今回のドリフトの原因である
