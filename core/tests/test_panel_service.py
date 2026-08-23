"""What the memory window may do to what Lumi remembers (ADR-042).

**docs/architecture/ui.md §5b / docs/contracts/privacy.md §5.**

Two properties here are the reason the window exists at all:

* **Correcting a memory supersedes it.** The wording Lumi had before stays readable, so
  "前はこう言ってたよね" survives the user fixing a typo in it.
* **Deleting means deleting.** `forget` and `erase` remove rows, and they remove the
  search index with them — a memory that is gone but still findable by its own text is
  the failure this file is written to catch.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from lumi.memory.records import AssertionMode, MemoryCandidate, MemoryType
from lumi.memory.store import MemoryStore
from lumi.memory.vectors import MemoryIndex, document_text
from lumi.panel.service import ERASE_CONFIRMATION, PanelService
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.audit import AUDIT_SCHEMA
from lumi.storage.events import EVENTS_SCHEMA
from lumi.storage.memory import EMBEDDING_DIMENSION, Episode, EpisodeStore, Utterance, open_memory
from lumi.storage.retention import RetentionService, Target
from lumi.storage.sqlite import IN_MEMORY, Database, one
from lumi.transport.methods import INBOUND_METHODS
from lumi.transport.server import RequestRefused

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class Rig:
    def __init__(self) -> None:
        self.db = open_memory(IN_MEMORY)
        self.events = Database.open(IN_MEMORY, EVENTS_SCHEMA)
        self.events.migrate()
        self.audit = Database.open(IN_MEMORY, AUDIT_SCHEMA)
        self.audit.migrate()
        self.store = MemoryStore(self.db)
        self.index = MemoryIndex(self.db)
        self.episodes = EpisodeStore(self.db)
        self.retention = RetentionService(memory=self.db, events=self.events, audit=self.audit)
        self.settings_seen: list[dict[str, Any]] = []
        self.service = PanelService(
            store=self.store,
            index=self.index,
            episodes=self.episodes,
            retention=self.retention,
            settings_update=self._settings,
            clock=lambda: NOW,
        )

    async def _settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.settings_seen.append(payload)
        return {}

    async def said(self, utterance_id: str, text: str = "Factorio 面白い") -> str:
        if await self.episodes.episode("e1") is None:
            await self.episodes.open_episode(Episode(id="e1", session_id="s1", started_at=NOW))
        await self.episodes.append(
            Utterance(
                id=utterance_id,
                episode_id="e1",
                turn_index=int(utterance_id[1:]),
                speaker="user",
                text=text,
                provenance_class=ProvenanceClass.TRUSTED,
                trust_level=TrustLevel.TRUSTED,
                occurred_at=NOW,
            )
        )
        return utterance_id

    async def remember(
        self, subject: str, content: str, *, utterance: str = "u1", when: datetime = NOW
    ) -> str:
        await self.said(utterance)
        record = await self.store.write(
            MemoryCandidate(
                type=MemoryType.SEMANTIC,
                subject=subject,
                content=content,
                assertion_mode=AssertionMode.USER_STATED,
                provenance_class=ProvenanceClass.TRUSTED,
                trust_level=TrustLevel.TRUSTED,
                evidence_ref=(utterance,),
            ),
            now=when,
        )
        await self.index.upsert(
            [(record.id, document_text(record), np.ones(EMBEDDING_DIMENSION, dtype=np.float32))]
        )
        return record.id

    def count(self, table: str) -> int:
        with self.db.transaction() as conn:
            return int(one(conn.execute(f"SELECT COUNT(*) FROM {table}"))[0])

    def close(self) -> None:
        self.db.close()
        self.events.close()
        self.audit.close()


@pytest.fixture
def rig() -> Iterator[Rig]:
    fixture = Rig()
    try:
        yield fixture
    finally:
        fixture.close()


# ── Routes ───────────────────────────────────────────────────


def test_every_registered_route_is_on_the_contract(rig: Rig) -> None:
    """**Registration is the allowlist** (ADR-028), so it is what has to match the wire."""
    registered: list[str] = []

    class Recorder:
        def on_request(self, method: str, handler: Any) -> None:
            registered.append(method)

    rig.service.register(Recorder())  # type: ignore[arg-type]

    assert registered == [method for method in INBOUND_METHODS if method.startswith("panel.")]


# ── Reading ──────────────────────────────────────────────────


async def test_the_newest_memories_come_first(rig: Rig) -> None:
    await rig.remember("user.hobby", "ユーザーは Factorio が好き", utterance="u1")
    await rig.remember(
        "user.work", "ユーザーは Rust を書く", utterance="u2", when=NOW + timedelta(hours=1)
    )

    answer = await rig.service.search({})

    assert [item["subject"] for item in answer["items"]] == ["user.work", "user.hobby"]
    assert answer["total"] == 2


async def test_history_is_hidden_unless_asked_for(rig: Rig) -> None:
    """★ **What Lumi used to believe is not what it believes.**

    A superseded record is still the truth about the past, and still worth reading — but
    a list that mixes it with the current belief cannot be used to answer "what does it
    think about me", which is the question people open this window with.
    """
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")
    await rig.store.rewrite(memory_id, content="ユーザーは Factorio に飽きた", now=NOW)

    current = await rig.service.search({})
    everything = await rig.service.search({"include_history": True})

    assert [item["content"] for item in current["items"]] == ["ユーザーは Factorio に飽きた"]
    assert "ユーザーは Factorio が好き" in [item["content"] for item in everything["items"]]


async def test_a_wildcard_in_the_search_box_is_a_character(rig: Rig) -> None:
    """`%` typed into a filter must match a `%`, not everything.

    Unescaped it reads as "match all", which looks exactly like a filter that silently
    stopped working.
    """
    await rig.remember("user.battery", "バッテリーは 100% で充電された", utterance="u1")
    await rig.remember("user.work", "ユーザーは Rust を書く", utterance="u2")

    assert (await rig.service.search({"query": "100%"}))["total"] == 1
    assert (await rig.service.search({"query": "%"}))["total"] == 1


async def test_a_page_says_how_many_there_are_in_total(rig: Rig) -> None:
    """**The total is of the filter, not of the page.** Without it the window cannot tell
    "these are all of them" from "these are the first few".
    """
    for index in range(3):
        await rig.remember(
            f"user.topic{index}",
            f"事実 {index}",
            utterance=f"u{index}",
            when=NOW + timedelta(hours=index),
        )

    page = await rig.service.search({"limit": 2})
    rest = await rig.service.search({"limit": 2, "offset": 2})

    assert [item["content"] for item in page["items"]] == ["事実 2", "事実 1"]
    assert [item["content"] for item in rest["items"]] == ["事実 0"]
    assert page["total"] == rest["total"] == 3


async def test_what_is_shown_includes_how_close_it_is_to_fading(rig: Rig) -> None:
    """`effective_salience` is a function of the clock, so it is computed on the way out."""
    await rig.remember("user.hobby", "ユーザーは Factorio が好き")
    later = PanelService(
        store=rig.store,
        index=rig.index,
        episodes=rig.episodes,
        retention=rig.retention,
        settings_update=rig._settings,
        clock=lambda: NOW + timedelta(days=60),
    )

    fresh = (await rig.service.search({}))["items"][0]
    faded = (await later.search({}))["items"][0]

    assert faded["effective_salience"] < fresh["effective_salience"]
    assert faded["base_salience"] == fresh["base_salience"]


# ── Correcting ───────────────────────────────────────────────


async def test_a_correction_supersedes_and_is_trusted(rig: Rig) -> None:
    """★ **Invariant 7's escalation, reached the only way it can be reached.**

    The user typed this sentence. Nothing is more strongly grounded than that, and the
    record says so — but it says so because `MemoryStore.rewrite` wrote it, not because a
    request handler decided to trust its own input.
    """
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")

    answer = await rig.service.edit({"id": memory_id, "content": "ユーザーは Factorio に飽きた"})

    corrected = answer["memory"]
    assert corrected["assertion_mode"] == AssertionMode.USER_CONFIRMED.value
    assert corrected["trust_level"] == TrustLevel.TRUSTED.value
    old = await rig.store.get(memory_id)
    assert old is not None
    assert old.content == "ユーザーは Factorio が好き"
    assert old.superseded_by == corrected["id"]


async def test_a_correction_drops_the_old_vector(rig: Rig) -> None:
    """★ **The corrected sentence must stop being findable.**

    The index still holds the text that was just replaced. Leaving it until the next
    indexing pass means a search can return the wording the user came here to fix, which
    reads as the correction not having been saved.
    """
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")
    assert await rig.index.count() == 1

    await rig.service.edit({"id": memory_id, "content": "ユーザーは Factorio に飽きた"})

    assert await rig.index.count() == 0


async def test_an_empty_correction_is_refused(rig: Rig) -> None:
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")
    with pytest.raises(RequestRefused, match="content_required"):
        await rig.service.edit({"id": memory_id, "content": "   "})


async def test_confirming_says_who_said_so(rig: Rig) -> None:
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")

    answer = await rig.service.confirm({"id": memory_id})

    assert answer["memory"]["assertion_mode"] == AssertionMode.USER_CONFIRMED.value
    assert answer["memory"]["trust_level"] == TrustLevel.TRUSTED.value


# ── Deleting ─────────────────────────────────────────────────


async def test_forgetting_removes_the_row_and_the_index_entry(rig: Rig) -> None:
    """★ **Deleted and still searchable is the failure that matters.**

    `memory_fts` holds the memory's own text. A delete that leaves it behind means the
    sentence can still be found by typing it — deleted from the table, present on screen.
    """
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")

    answer = await rig.service.forget({"id": memory_id})

    assert answer["deleted"] == 1
    assert rig.count("memories") == 0
    assert rig.count("memory_evidence") == 0
    assert await rig.index.count() == 0


async def test_forgetting_something_that_is_not_there_is_refused(rig: Rig) -> None:
    """**Never answers "deleted 0" as if it worked.** The window would show success."""
    with pytest.raises(RequestRefused, match="no_such_memory"):
        await rig.service.forget({"id": "nope"})


async def test_a_deletion_is_recorded(rig: Rig) -> None:
    """privacy.md §5: the counts survive, the content does not."""
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")
    await rig.service.forget({"id": memory_id})

    with rig.audit.transaction() as conn:
        rows = conn.execute("SELECT target, count FROM deletion_log").fetchall()

    assert [(str(row[0]), str(row[1])) for row in rows] == [(Target.MEMORIES.value, "1")]


# ── Erasing everything ───────────────────────────────────────


async def test_the_preview_lists_every_row_including_the_empty_ones(rig: Rig) -> None:
    """★ **Zero is a fact about the row, not a reason to hide it** (ui.md §5b).

    A confirmation screen that only lists what happens to be non-empty cannot be used to
    check that a category was not forgotten — which is the one thing it is for.
    """
    await rig.remember("user.hobby", "ユーザーは Factorio が好き")

    preview = await rig.service.erase_preview({})

    counts = {row["target"]: row["count"] for row in preview["targets"]}
    assert counts[Target.MEMORIES.value] == 1
    assert counts[Target.EPISODES.value] == 1
    assert counts[Target.EVENTS.value] == 0
    assert counts[Target.AUDIT.value] == 0


async def test_erasing_needs_the_confirmation_from_the_preview(rig: Rig) -> None:
    await rig.remember("user.hobby", "ユーザーは Factorio が好き")

    with pytest.raises(RequestRefused, match="confirmation_required"):
        await rig.service.erase({})

    assert rig.count("memories") == 1


async def test_erasing_everything_leaves_nothing_behind(rig: Rig) -> None:
    """★ **"全部消して" means the §2 table, all of it** — including the search index.

    The index is derived, which is why ordinary deletion leaves it alone. Here there is
    nothing left to derive it from, and `memory_fts` would be the only place the user's
    sentences still existed.
    """
    await rig.remember("user.hobby", "ユーザーは Factorio が好き")

    answer = await rig.service.erase({"confirmation": ERASE_CONFIRMATION})

    assert {row["target"] for row in answer["targets"]} == {target.value for target in Target}
    for table in (
        "memories",
        "memory_evidence",
        "memory_sources",
        "memory_fts",
        "utterances",
        "episodes",
    ):
        assert rig.count(table) == 0, table
    assert await rig.index.count() == 0


async def test_erasing_keeps_the_record_that_it_happened(rig: Rig) -> None:
    """**The deletion log is not part of what gets erased** (privacy.md §5).

    Losing it would mean the erasure itself left no trace — and "it was erased" is the
    one claim the user has to be able to check afterwards.
    """
    await rig.remember("user.hobby", "ユーザーは Factorio が好き")
    await rig.service.erase({"confirmation": ERASE_CONFIRMATION})

    with rig.audit.transaction() as conn:
        rows = conn.execute("SELECT target, count, trigger FROM deletion_log").fetchall()

    assert rows
    assert {str(row[2]) for row in rows} == {"erase"}


# ── Export ───────────────────────────────────────────────────


async def test_the_export_is_plain_text_and_says_so(
    rig: Rig, tmp_path: Any, monkeypatch: Any
) -> None:
    """**The window has to be able to warn before it happens**, so the answer says it too."""
    monkeypatch.setattr("lumi.paths.data_dir", lambda: tmp_path)
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")

    answer = await rig.service.export({})

    assert answer["plaintext"] is True
    assert answer["count"] == 1
    written = json.loads(
        await asyncio.to_thread(Path(str(answer["path"])).read_text, encoding="utf-8")
    )
    assert written["memories"][0]["id"] == memory_id
    assert written["memories"][0]["content"] == "ユーザーは Factorio が好き"


async def test_the_export_includes_what_was_superseded(
    rig: Rig, tmp_path: Any, monkeypatch: Any
) -> None:
    """A copy of what Lumi remembers that silently omits the corrections is not a copy."""
    monkeypatch.setattr("lumi.paths.data_dir", lambda: tmp_path)
    memory_id = await rig.remember("user.hobby", "ユーザーは Factorio が好き")
    await rig.store.rewrite(memory_id, content="ユーザーは Factorio に飽きた", now=NOW)

    answer = await rig.service.export({})

    assert answer["count"] == 2
