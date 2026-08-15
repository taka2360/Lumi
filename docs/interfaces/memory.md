# Interface: Memory

親: [DESIGN.md](../DESIGN.md) / 設計: [../architecture/memory.md](../architecture/memory.md) / [ADR-004](../decisions/ADR-004-sqlite-vec-memory.md)

---

## MemoryRecord

```python
@dataclass(frozen=True)
class MemoryRecord:
    id: MemoryId
    type: MemoryType              # episodic | semantic | procedural
    subject: str                  # 誰/何についての記憶か
    content: str

    # ── 認識論的メタデータ ──────────────────
    assertion_mode: AssertionMode
    evidence_ref: list[UtteranceId]
    confidence: float             # 0.0-1.0

    # ── 信頼 ────────────────────────────────
    provenance_class: ProvenanceClass
    trust_level: TrustLevel

    # ── 減衰 ────────────────────────────────
    base_salience: float
    created_at: datetime
    last_accessed: datetime
    access_count: int
    archived_at: datetime | None

    # ── バージョン付き信念 ──────────────────
    valid_from: datetime
    superseded_by: MemoryId | None

    source_episode_ids: list[EpisodeId]
    embedding_model_id: str       # 再埋め込みが必要かの判定用
```

### `assertion_mode`

```python
class AssertionMode(Enum):
    USER_CONFIRMED = "user_confirmed"   # ユーザーが記憶UIで確認した（最強）
    USER_STATED    = "user_stated"      # ユーザーが明示的に述べた
    INFERRED       = "inferred"         # 会話から推測した
    SELF_GENERATED = "self_generated"   # Lumi 自身の推測・想像
    EXTERNAL       = "external"         # 外部データ由来
```

**`confidence` とは別軸。** 「強く確信しているが、それは自分の推測」がありうる。

強弱の順序（矛盾解決に使う）:
```
USER_CONFIRMED > USER_STATED > INFERRED > SELF_GENERATED > EXTERNAL
```

---

## MemoryStore

```python
class MemoryStore(Protocol):
    async def write(self, candidate: MemoryCandidate) -> MemoryRecord: ...

    async def supersede(self, old_id: MemoryId, new: MemoryCandidate) -> MemoryRecord:
        """既存を上書きせず、superseded_by で繋ぐ。"""

    async def archive(self, id: MemoryId) -> None:
        """物理削除しない。"""

    async def purge(self, id: MemoryId) -> None:
        """物理削除。ユーザーの明示的操作でのみ呼ばれる。"""

    async def confirm(self, id: MemoryId) -> MemoryRecord:
        """assertion_mode を USER_CONFIRMED に、trust_level を TRUSTED に昇格。
        Invariant 7 の唯一の昇格経路。記憶UIのハンドラからのみ呼ばれる。"""

    async def get(self, id: MemoryId) -> MemoryRecord | None: ...
    async def find_conflicts(self, subject: str, content: str) -> list[MemoryRecord]: ...
```

### `confirm` が唯一の昇格経路

**この関数以外に `trust_level = TRUSTED` を書く箇所を作らない**（Invariant 7）。
静的検査とテストで保証する。

---

## VectorStore

```python
class VectorStore(Protocol):
    async def upsert(self, id: MemoryId, vector: Vector) -> None: ...
    async def delete(self, id: MemoryId) -> None: ...
    async def search(self, query: Vector, k: int,
                     filter: VectorFilter | None) -> list[ScoredId]: ...
    def dimension(self) -> int: ...
```

### 実装

| 実装 | 状態 |
|---|---|
| `SqliteVecStore` | **Phase 2 で実装** |
| `QdrantStore` | 将来。規模が問題になったら |

**この interface があることで、sqlite-vec の選択が可逆になっている。**

### なぜ Qdrant を最初から使わないのか

| | sqlite-vec | Qdrant |
|---|---|---|
| プロセス | **なし**（ライブラリ） | 常駐プロセス |
| RAM | SQLite の範囲内 | 数百MB〜 |
| 規模 | 数十万ベクトルで十分実用 | 数百万〜 |
| 単一ユーザーデスクトップ | **適切** | 過剰 |

RAM を1つでも節約したい（ローカル LLM に回したい）ため、sqlite-vec を選ぶ。

---

## Retriever

```python
class Retriever(Protocol):
    async def retrieve(
        self,
        query: str,
        token_budget: int,
        now: datetime,
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class RetrievalResult:
    selected: list[ScoredMemory]
    dropped: list[ScoredMemory]      # 予算に入らなかったもの
    breakdown: dict[MemoryId, ScoreBreakdown]   # Inspector 用


@dataclass(frozen=True)
class ScoreBreakdown:
    similarity: float
    recency: float
    effective_salience: float
    assertion_weight: float
    total: float
```

### `dropped` と `breakdown` を返す理由

**「なぜこの記憶が使われたのか / 使われなかったのか」を Inspector で見られるようにする。**

自律エージェントで最も重要なデバッグ機能。これが無いと Phase 6 でチューニング不能になる。

### スコアリング

**式と重みは [../architecture/memory.md](../architecture/memory.md) §7 が唯一の定義場所。**

interface 上の契約は「`ScoreBreakdown` の各項を返すこと」だけ。内訳が見えないと Inspector でチューニングできない。

### 予算超過時の切り落とし

**決定論的。** スコア順に詰めて、入らないものを `dropped` に入れる。LLM に「適当に切って」をさせない。

---

## WorkingMemory

```python
class WorkingMemory(Protocol):
    """in-process。セッション単位。永続化しない。"""
    def append(self, turn: Turn) -> None: ...
    def recent(self, n: int) -> list[Turn]: ...
    def token_count(self) -> int: ...
    def compact(self, target_tokens: int) -> None:
        """古いターンを要約に置換。直近 N ターンは保護する。"""
    def clear(self) -> None: ...
```

### `compact` を実際に配線する

AIRI は `compactConversationEntries()` を実装しているが、**`index.ts` からも `package.json` の exports からも公開されておらず、呼び出し元がテストのみ**という未配線の死にコードになっている。

Lumi では Phase 1 から `PromptAssembly` の経路に組み込み、**トークン予算超過時に必ず呼ばれる**ようにする。

---

## ReflectionJob

```python
class ReflectionJob(Protocol):
    async def run(self, episodes: list[Episode]) -> list[MemoryRecord]:
        """LLM 抽出 → 重複統合 → 矛盾検出 → assertion_mode 検証
        → provenance 付与 → salience 補正 → 書き込み"""
```

### 実行形態

**`Job(kind=reflection, actor=system, uses_inference=True)` として走る。Activity ではない。**

```python
async with arbiter.inference_lease(job) as lease:
    candidates = await llm.extract(episodes, cancel_token=lease.token)
```

foreground が推論を要求したら lease は revoke され、Job は進捗を捨てて中断する。
→ [ADR-018](../decisions/ADR-018-foreground-and-jobs.md), [../architecture/memory.md](../architecture/memory.md) §4

### 起動タイミング / salience の補正

→ [../architecture/memory.md](../architecture/memory.md) §4

---

## Forgetting

減衰式・τ・archive の規則 → [../architecture/memory.md](../architecture/memory.md) §5

interface 上の契約:

- `effective_salience(m, now)` は**純粋関数**であること（テスト可能性）
- `floor` を下回ったら `archive()`
- **`purge()` はユーザーの明示的操作でのみ**呼ばれる

---

## マイグレーション

```sql
_schema_version (component, version, applied_at)
```

**ユーザーの PC に保存された記憶は、半年後の新バージョンでも読めなければならない。**

| 変更 | 対応 |
|---|---|
| カラム追加 | デフォルト値付きで ALTER |
| 埋め込みモデル変更 | `embedding_model_id` の不一致を検出 → バックグラウンドで再埋め込み |
| スキーマ大改変 | 移行スクリプト。**失敗したら旧 DB をバックアップして中断**（データを失わない） |

---

## テスト

| # | テスト |
|---|---|
| 1 | `effective_salience` の減衰が期待通り |
| 2 | archive された記憶が通常検索にヒットしない |
| 3 | archive が物理削除でない |
| 4 | `supersede` が既存を上書きせず履歴を残す |
| 5 | 矛盾検出が `assertion_mode` の強弱を正しく比較する |
| 6 | **`confirm()` 以外の経路で `trust_level = TRUSTED` にならない**（Invariant 7） |
| 7 | Retriever が予算超過時に決定論的に切り落とす |
| 8 | `dropped` と `breakdown` が正しく返る |
| 9 | `compact()` が PromptAssembly から実際に呼ばれる（未配線でないこと） |
| 10 | `VectorStore` の実装を差し替えても Retriever が動く |
| 11 | 埋め込みモデル変更が `model_id` の不一致で検出される |
| 12 | マイグレーションが旧バージョンの DB を読める |
| 13 | マイグレーション失敗時に旧 DB がバックアップされる |
