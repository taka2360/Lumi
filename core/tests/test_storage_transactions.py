"""What a transaction does when the caller is cancelled, and when two callers collide.

**Characterization tests.** They describe what Core does today, so that moving the
`to_thread` + `transaction()` pairs behind one helper can be shown to change nothing.
They are not a wish list — where the behaviour is surprising, the surprise is pinned.

The two surprises worth pinning:

- **`asyncio.to_thread` is not cancellable.** Cancelling the awaiting coroutine
  abandons the *await*, not the work. The thread runs to completion and the
  transaction commits. Anything that assumed "cancel the task, lose the write" would
  be wrong about the retention job and about every memory write.
- **One connection, one lock** (`storage/sqlite.py`). Concurrent writers do not
  interleave and do not raise; they queue. This is what makes it safe to hand blocking
  DB work to a thread pool at all.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any

import apsw
import pytest

from lumi.storage.events import EVENTS_SCHEMA
from lumi.storage.sqlite import Database, StorageError, one

KEY = "ab" * 32

INSERT = (
    "INSERT INTO events"
    " (id, stream_key, sequence_id, type, payload, correlation_id, occurred_at)"
    " VALUES (?, 's', ?, 't', '{}', 'c', 'now')"
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database.open(tmp_path / "events.db", EVENTS_SCHEMA, key=KEY)
    try:
        yield database
    finally:
        database.close()


def count(database: Database) -> int:
    with database.transaction() as conn:
        return int(one(conn.execute("SELECT COUNT(*) FROM events"))[0])


async def by_hand[T](database: Database, work: Callable[[apsw.Connection], T]) -> T:
    """How every DAO spelled it out before `in_transaction`: the whole transaction
    inside one thread hop.

    **Not `with transaction(): await ...`** — that would hold a `threading.Lock` across
    an await point, and the transaction boundary would no longer match the thread's.
    """

    def once() -> T:
        with database.transaction() as conn:
            return work(conn)

    return await asyncio.to_thread(once)


async def by_helper[T](database: Database, work: Callable[[apsw.Connection], T]) -> T:
    return await database.in_transaction(work)


#: **Both spellings, every test.** The helper is only worth having if it is the hand-written
#: form exactly — including where that form is surprising.
type Runner = Callable[[Database, Callable[[apsw.Connection], object]], Coroutine[Any, Any, object]]

runners = pytest.mark.parametrize("run", [by_hand, by_helper], ids=["by_hand", "in_transaction"])


@runners
async def test_the_work_and_its_commit_happen_in_one_thread_hop(db: Database, run: Runner) -> None:
    inside: list[int] = []

    def work(conn: apsw.Connection) -> None:
        conn.execute(INSERT, ("a", 1))
        inside.append(threading.get_ident())

    await run(db, work)
    assert inside and inside[0] != threading.get_ident()
    assert count(db) == 1


@runners
async def test_an_exception_rolls_back_and_reaches_the_caller(db: Database, run: Runner) -> None:
    """The exception type is not swallowed or wrapped. **The row is gone.**"""

    def work(conn: apsw.Connection) -> None:
        conn.execute(INSERT, ("a", 1))
        raise StorageError("boom")

    with pytest.raises(StorageError, match="boom"):
        await run(db, work)
    assert count(db) == 0


@runners
async def test_the_connection_still_works_after_a_rollback(db: Database, run: Runner) -> None:
    """A failed transaction must not leave `BEGIN` open. **The next writer would hang.**"""

    def failing(conn: apsw.Connection) -> None:
        conn.execute(INSERT, ("a", 1))
        raise StorageError("boom")

    with pytest.raises(StorageError):
        await run(db, failing)
    await run(db, lambda conn: conn.execute(INSERT, ("b", 1)))
    assert count(db) == 1


@runners
async def test_cancelling_the_caller_does_not_undo_the_write(db: Database, run: Runner) -> None:
    """**Cancellation abandons the await, not the work** — `to_thread` cannot be cancelled.

    Core relies on this without saying so. The retention job deletes across three
    databases inside one hop; if a cancel could stop it halfway, "erase everything"
    would be able to erase some things.
    """
    inside = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def work(conn: apsw.Connection) -> None:
        conn.execute(INSERT, ("a", 1))
        inside.set()
        release.wait(5)
        finished.set()

    task: asyncio.Task[object] = asyncio.create_task(run(db, work))
    await asyncio.to_thread(inside.wait, 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The worker is still holding the lock. Let it finish before looking.
    release.set()
    await asyncio.to_thread(finished.wait, 5)
    assert finished.is_set()
    assert count(db) == 1, "the committed row survived the cancel"


@runners
async def test_concurrent_writers_are_serialized_rather_than_interleaved(
    db: Database, run: Runner
) -> None:
    """One connection, one lock. **Queued, not raising** ("cannot start a transaction
    within a transaction" is what happens without it).
    """
    order: list[str] = []
    guard = threading.Lock()

    def work(tag: str, sequence: int) -> Callable[[apsw.Connection], None]:
        def run(conn: apsw.Connection) -> None:
            with guard:
                order.append(f"enter:{tag}")
            conn.execute(INSERT, (tag, sequence))
            with guard:
                order.append(f"exit:{tag}")

        return run

    await asyncio.gather(*(run(db, work(tag, n)) for n, tag in enumerate(("a", "b", "c"), start=1)))

    assert count(db) == 3
    # Every enter is immediately followed by its own exit.
    for index in range(0, len(order), 2):
        assert order[index].removeprefix("enter:") == order[index + 1].removeprefix("exit:")
