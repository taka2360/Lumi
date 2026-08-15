# ADR-004: Memory を SQLite + sqlite-vec とし `VectorStore` で抽象化する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../architecture/memory.md](../architecture/memory.md), [../interfaces/memory.md](../interfaces/memory.md) |

---

## Decision

記憶の永続化に **SQLite + sqlite-vec + FTS5** を使う。**単一ファイル、追加プロセスゼロ。**

`VectorStore` インターフェースで隔離し、将来の差し替え（Qdrant 等）を妨げない。

Embedding は **ONNX / CPU 実行**とし、VRAM を消費しない。

---

## Reason

### RAM を1つでも節約したい

本プロジェクトはローカル LLM（6.5GB VRAM）を動かす。RAM も VRAM も AI に回したい。

| | sqlite-vec | Qdrant |
|---|---|---|
| プロセス | **なし**（ライブラリ） | 常駐プロセス |
| RAM | SQLite の範囲内 | 数百MB〜 |
| 起動 | 不要 | 起動・停止の管理が必要 |
| バックアップ | **ファイルコピー** | スナップショット API |

### 規模が足りている

単一ユーザーのデスクトップアプリで、数年使っても記憶は数万〜数十万件のオーダー。

**sqlite-vec は数十万ベクトルなら十分実用的。** 数百万を超えたら Qdrant を検討すればよく、そのときは `VectorStore` を差し替える。

### 単一ファイルの運用上の利点

- バックアップがファイルコピー
- ユーザーが「記憶を消したい」と思ったらファイルを消せば済む
- 別マシンへの移行が容易
- **トランザクションが記憶と監査ログと Event で共有できる**（採番と永続化の原子性、[ADR-010](ADR-010-signal-vs-domain-event.md)）

最後の点は重要で、複数のストアに分けると DomainEvent の採番と永続化を同一トランザクションにできなくなる。

### FTS5 が同居する

ハイブリッド検索（vector + キーワード）に FTS5 が必要。SQLite なら同じファイル・同じトランザクション内で完結する。

---

## Alternatives

### A. Qdrant

**利点:** 数百万ベクトル規模。フィルタリングが高機能。HNSW の実装が成熟
**欠点:** 常駐プロセス + 数百MB RAM。単一ユーザーのデスクトップには過剰。起動・停止・バージョン管理の運用が増える

### B. PostgreSQL + pgvector

**利点:** AIRI の telegram-bot が採用。実績がある
**欠点:** サーバが要る。非目標（クラウドサービスを作らない）

### C. DuckDB WASM（AIRI の stage 側の選択）

**利点:** ブラウザで動く
**欠点:** Python Core には不要な複雑さ。**AIRI では実際には `memory_test` という空テーブルを1つ作るだけで、INSERT も SELECT も無い**（動作確認用のプレースホルダのまま）

### D. ベクトル検索を使わない（キーワードのみ）

**利点:** Embedding Provider が不要。最も軽い
**欠点:** 「Factorio の話」で「工場ゲーム」の記憶が引けない。記憶の有用性が大きく下がる

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 | 緩和 |
|---|---|---|
| スケール上限 | 数百万ベクトルは厳しい | `VectorStore` で差し替え可能 |
| フィルタリング機能 | Qdrant ほど高機能でない | SQL の WHERE と組み合わせる |
| sqlite-vec の成熟度 | Qdrant より新しい | 単純な用途（cosine 検索）に限定して使う |

### 得るもの

- 常駐プロセスとRAMを1つ減らせる
- バックアップ・移行が容易
- トランザクションが記憶・監査・Event で共有できる

---

## Consequences

### `VectorStore` interface が必須になる

```python
class VectorStore(Protocol):
    async def upsert(self, id: MemoryId, vector: Vector) -> None: ...
    async def delete(self, id: MemoryId) -> None: ...
    async def search(self, query: Vector, k: int,
                     filter: VectorFilter | None) -> list[ScoredId]: ...
    def dimension(self) -> int: ...
```

**この interface があることで、sqlite-vec の選択が可逆になっている。** 抽象化のコストは小さく、変更の可能性は実在するため、設計原則7に照らして正当化される。

### Embedding を CPU 実行にする

VRAM を LLM に全振りするため、Embedding は ONNX / CPU で動かす。

これは sqlite-vec の選択とは独立だが、「デスクトップのリソースを AI に回す」という同じ動機から来ている。

### 埋め込みモデル変更への対処

`memories.embedding_model_id` を持ち、モデル変更時に再埋め込みが必要なことを検出する。

AIRI の telegram-bot は 1536/1024/768 の3次元を並列カラムで持つ設計を採っているが、**Lumi は単一次元 + 再埋め込み**にする。デスクトップ単一ユーザーなら再計算コストが許容できるため。

### マイグレーションが必須になる

単一ファイルであることは、**ユーザーの PC にファイルが残り続ける**ことを意味する。

```sql
_schema_version (component, version, applied_at)
```

**半年後の新バージョンでも読めなければならない。** マイグレーション失敗時は旧 DB をバックアップして中断する（データを失わない）。

### この判断を見直す条件

- ベクトル数が数百万を超える
- sqlite-vec のパフォーマンスが実用に耐えなくなる
- フィルタリング要件が SQL の WHERE で表現できなくなる

**その場合は `VectorStore` の Qdrant 実装を追加し、新しい ADR を書く。**
