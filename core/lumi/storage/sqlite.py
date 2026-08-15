"""SQLite 接続とマイグレーション。

**Phase 1 で持つのは DomainEvent の永続化だけ。** sqlite-vec / FTS5 / 記憶テーブルは Phase 2。
先に作らない理由は、記憶のスキーマがプライバシー方針（docs/roadmap.md Phase 2 の 🔴）
に依存し、方針を決める前に書くと作り直しになるため。

## Phase 1 で永続化するもの / しないもの

| する | しない |
|---|---|
| Kernel の事実（Activity の開始・終了、Tool の3段記録、権限判断） | **発話の本文** |

**DomainEvent の payload に発話テキストを入れない。** Phase 1 の Working Memory は
セッション内のメモリ上にあり、会話の永続化は Phase 2（`contracts/privacy.md` を書いてから）
始める。ここで先に本文を書き始めると、方針が決まる前に「消し方の分からないログ」が育つ。

## トランザクション

`isolation_level=None`（autocommit）にして、**`BEGIN IMMEDIATE` を明示する。**
Python の暗黙トランザクションは DDL や SELECT の扱いが直感と違い、
「採番と永続化を同一トランザクションで」（docs/contracts/event-model.md）を
守れているかがコードから読めなくなる。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

#: このスキーマ版。マイグレーションを足したら **`_MIGRATIONS` の長さと一致する**。
SCHEMA_VERSION: Final = 1

#: index 0 を適用するとスキーマ版 1 になる。**既存の要素を書き換えない**（追記のみ）。
_MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    (
        """
        CREATE TABLE events (
            id             TEXT    PRIMARY KEY,
            stream_key     TEXT    NOT NULL,
            sequence_id    INTEGER NOT NULL,
            type           TEXT    NOT NULL,
            payload        TEXT    NOT NULL,
            correlation_id TEXT    NOT NULL,
            causation_id   TEXT,
            occurred_at    TEXT    NOT NULL,
            UNIQUE (stream_key, sequence_id)
        )
        """,
        "CREATE INDEX events_by_stream ON events (stream_key, sequence_id)",
        "CREATE INDEX events_by_correlation ON events (correlation_id)",
    ),
)


class StorageError(RuntimeError):
    """DB を開けない / スキーマが想定より新しい。**黙って劣化しない。**"""


class Database:
    """1本の接続。**書き込みはプロセス内で直列化する。**

    `check_same_thread=False` にしているのは、ブロッキング I/O を
    `asyncio.to_thread` に逃がす（イベントループを止めない）ため。
    その代わり、同時に触られないことを自前のロックで保証する。
    """

    __slots__ = ("_conn", "_lock")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: Path | str) -> Database:
        """開いてマイグレーションを適用する。`":memory:"` を渡せばテスト用。"""
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        except sqlite3.Error as error:
            raise StorageError(f"DB を開けない: {path}") from error

        connection.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            # WAL は :memory: では効かない（エラーにもならない）。
            connection.execute("PRAGMA journal_mode = WAL")

        database = cls(connection)
        database.migrate()
        return database

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """`BEGIN IMMEDIATE` 〜 `COMMIT`。失敗したら `ROLLBACK`。

        `IMMEDIATE` にするのは、書き込みロックを最初に取るため。
        遅延取得だと、採番の SELECT と INSERT の間に他の writer が入りうる。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def migrate(self) -> None:
        """未適用のマイグレーションを順に当てる。**下げる経路は作らない。**"""
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM _schema_version").fetchone()
            current = int(row[0]) if row else 0

            if current > SCHEMA_VERSION:
                # 新しい Lumi が作った DB を古い Lumi で開いた。**推測で動かさない。**
                raise StorageError(
                    f"DB のスキーマ版 {current} がこの Lumi ({SCHEMA_VERSION}) より新しい"
                )

            for statements in _MIGRATIONS[current:]:
                for statement in statements:
                    conn.execute(statement)

            if row is None:
                conn.execute("INSERT INTO _schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE _schema_version SET version = ?", (SCHEMA_VERSION,))

        if current != SCHEMA_VERSION:
            log.info("storage.migrated", from_version=current, to_version=SCHEMA_VERSION)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
