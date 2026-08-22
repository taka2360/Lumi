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
    async def write(self, candidate: MemoryCandidate, *, now: datetime) -> MemoryRecord: ...

    async def supersede(self, old_id: MemoryId, new: MemoryCandidate, *,
                        now: datetime) -> Reconciled:
        """既存を上書きせず、superseded_by で繋ぐ。矛盾の episodic 記録を同じ
        トランザクションで書くので、戻り値は新レコード単体ではない。"""

    async def reconcile(self, candidate: MemoryCandidate, *, now: datetime) -> Reconciled:
        """重複・矛盾を見たうえで書く。判定は contradiction.resolve（純粋関数）。"""

    async def archive(self, id: MemoryId, *, now: datetime) -> None:
        """物理削除しない。"""

    async def archive_faded(self, *, now: datetime) -> Sequence[MemoryId]:
        """floor を下回ったものを archive する。**起動ごとに1回**。"""

    async def touch(self, ids: Sequence[MemoryId], *, now: datetime) -> None:
        """検索でヒットしたことを記録する（access_boost の入力）。**内容は記録しない。**"""

    async def confirm(self, id: MemoryId, *, now: datetime) -> MemoryRecord:
        """assertion_mode を USER_CONFIRMED に、trust_level を TRUSTED に昇格。
        Invariant 7 の唯一の昇格経路。記憶UIのハンドラからのみ呼ばれる。"""

    async def get(self, id: MemoryId) -> MemoryRecord | None: ...
    async def live(self, subject: str) -> Sequence[MemoryRecord]: ...
    async def find_conflicts(self, subject: str, content: str) -> Sequence[MemoryRecord]:
        """同一 subject で内容の違う **semantic** な信念。**出来事は信念と矛盾しない。**"""


@dataclass(frozen=True)
class Reconciled:
    record: MemoryRecord
    resolution: Resolution          # new | duplicate | supersede | keep_weak
    superseded_id: MemoryId | None = None
    note: MemoryRecord | None = None   # 矛盾があったことの episodic 記録
```

> **〔2026-08-22 / 2d〕この節を実装に合わせて書き直した。** `supersede()` の戻り値が
> `Reconciled` になり、`reconcile()` / `touch()` / `live()` / `archive_faded()` が加わり、
> `purge()` が外れた（下記）。**時刻は必ず引数で渡す**（`.claude/rules/tests.md`）。

### `purge()` はここに無い〔2026-08-22 / 2d〕

**物理削除は `lumi.storage.retention` にある。** ユーザーデータを消す `DELETE` を
1ファイルに閉じる境界（[../contracts/privacy.md](../contracts/privacy.md) §5）は、
**記憶 UI の削除だけが別のクラスにあった時点で検査できなくなる**。
記憶ストアが持つのは `archive()`（`UPDATE`）までで、消す責任は持たない。

削除は保持期間の削除と同じく **`deletion_log` に記録される**（件数のみ。中身は残さない）。

### `write()` は `user_confirmed` を受け付けない〔2026-08-22 / 2d〕

`confirm()` が唯一の昇格経路である以上、**候補として `user_confirmed` を名乗ることも許さない**。
許すと、抽出側が「ユーザーが言ったのだから確認済みでよいはず」と判断できてしまい、
昇格経路が2本になる。`write()` は拒否する（fail-closed）。

### `confirm` が唯一の昇格経路

**この関数以外に `trust_level = TRUSTED` を書く箇所を作らない**（Invariant 7）。
静的検査とテストで保証する。

### 根拠と信頼は候補の申告をそのまま採らない〔2026-08-22 / 2d〕

`write()` は `evidence_ref` の発話を実際に読み、**その `trust_level` と候補の申告を join する。**
join は上げない（`taint ⊒ trusted`）ので、これは伝播であって昇格ではない。
存在しない `evidence_ref` を持つ候補は**拒否する**（[../architecture/memory.md](../architecture/memory.md) §4 の
「assertion_mode の検証」）。

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
| **`MemoryIndex`**（`lumi/memory/vectors.py`） | **2e で実装済み。** vec0（640 / cosine）と FTS5（trigram）を1つのクラスが持つ |
| `QdrantStore` | 将来。規模が問題になったら |

> **〔2026-08-22 / 2e〕ベクトルとキーワードを別クラスに分けなかった。**
> 埋め込みが使えるときは両方を union し、**使えないとき（`embedder=None`、または埋め込みの失敗）は
> ベクトル検索を飛ばして keyword と recent で続ける**。
> どちらの場合も片方だけ差し替えられる形にしても使い道が無い。
> **どちらも「記憶を見つける手段」であって、記憶そのものではない**——
> ここから行を消しても失われるのは findability だけで、信念は `memories` に残る。

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
        *,
        token_budget: int,
        now: datetime,
    ) -> RetrievalResult: ...

    async def record_use(self, result: RetrievalResult, *, now: datetime) -> None:
        """選ばれた記憶を「思い出した」として数える（access_boost の入力）。
        **返事の後に呼ぶ。** 書き込みをターンの待ち時間に載せない。"""


@dataclass(frozen=True)
class RetrievalResult:
    # **既定は空。** 候補が1件も無い経路は embed_ms と degraded だけを持って返る
    selected: tuple[ScoredMemory, ...] = ()
    dropped: tuple[ScoredMemory, ...] = ()   # 予算に入らなかったもの
    embed_ms: float = 0.0                 # クエリ埋め込みの実測。**クリティカルパス**
    degraded: bool = False                # 埋め込みが無い / どれかの検索が落ちた


@dataclass(frozen=True)
class ScoredMemory:
    record: MemoryRecord
    breakdown: ScoreBreakdown             # **1件ごとに持つ**（Inspector 用）


@dataclass(frozen=True)
class ScoreBreakdown:
    similarity: float
    recency: float
    effective_salience: float
    assertion_weight: float
    total: float
    sources: tuple[str, ...] = ()         # vector / keyword / recent
```

> **〔2026-08-22 / 2e〕`breakdown` は `ScoredMemory` が持つ。**
> `dict[MemoryId, ScoreBreakdown]` を別に返すと、**選ばれた記憶と内訳が別々に運ばれる**——
> 片方だけ絞り込んだコードが、対応の取れない2つのリストを作る。

### `dropped` と `breakdown` を返す理由

**「なぜこの記憶が使われたのか / 使われなかったのか」を Inspector で見られるようにする。**

自律エージェントで最も重要なデバッグ機能。これが無いと Phase 6 でチューニング不能になる。

### 実装〔2026-08-22 / 2e〕

**`record_use` が別メソッドなのは、書き込みを返事の前に置かないため。**
「候補に挙がった」と「実際に使われた」も別物で、予算で落ちたものは強化しない。

**`degraded = True` は「埋め込みが無い / どれかの検索が落ちた」。**
**source ごとに独立して失敗する**——FTS5 が落ちても、既に手元にあるベクトルの結果は捨てない。
検索は残った source だけで続き、**ターンは失敗しない**。

**予算はレコード単位のコスト関数で測る**（`cost: Callable[[MemoryRecord], int]`）。
プロンプトに載るのは根拠付きで整形された行であり（`agent.recall`）、
本文だけで見積もると**根拠の文言のぶんだけ確実に溢れる**。
どう整形するかは prompt 側の関心なので、**memory は関数を受け取るだけで、整形は知らない**。

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
    async def run(self) -> ReflectionReport:
        """LLM 抽出 → 重複統合 → 矛盾検出 → assertion_mode 検証
        → provenance 付与 → salience 補正 → 書き込み"""


@dataclass(frozen=True)
class ReflectionReport:
    written: int = 0
    superseded: int = 0
    duplicates: int = 0
    rejected: tuple[str, ...] = ()   # 受け付けなかった理由。**数だけでなく理由を残す**
    interrupted: bool = False        # revoke / エンジン失敗。**watermark は動いていない**
    episodes: int = 0
```

> **〔2026-08-23 / 2f〕対象の Episode は引数ではなく Job が選ぶ。**
> 「どこまで抽出したか」は `episodes.reflected_turns` にあり、
> **呼び出し側がそれを知っていると2箇所が同じ状態を持つ**ことになる。
> 戻り値がレコードの一覧でないのも同じ理由で、**書き込みは Job の中で完了している**——
> 一覧を返すと「呼び出し側が保存するのか」が曖昧になる。

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
- **物理削除はユーザーの明示的操作でのみ**行われ、実装は `lumi.storage.retention` にある（上記）

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
