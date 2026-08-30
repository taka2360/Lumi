"""What the panel windows may ask Core for (ADR-042).

The memory window is the reason this exists. It is **the only way a user can see, correct
or delete what Lumi believes about them**, and privacy.md §5 requires that the way out of
the database is as real as the way in.

## Two properties this file is responsible for

**Every escalation goes through `MemoryStore`.** Nothing here writes `TrustLevel.TRUSTED`
(Invariant 7) — `confirm` and `rewrite` do, inside the store, which is one of the three
files the static check allows. A handler that built a trusted record itself would be the
fourth, and the check exists precisely so that number does not drift upwards.

**Deleting goes through `RetentionService`.** It is the one file that may delete user data
(privacy.md §5, "実装"), and routing "forget this" around it would put a second deletion
path in the codebase — the first thing anyone auditing deletion would miss.

## What is not here

**No display strings.** Refusals travel as codes and the window owns the wording
(ADR-036), for the same reason Core never sends a sentence anywhere else: a locale change
has to reach every line on screen, including the ones nobody expected to be read.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from lumi import logging as lumi_logging
from lumi import paths
from lumi.memory import decay
from lumi.memory.browse import MemoryBrowser
from lumi.memory.records import MemoryRecord
from lumi.memory.store import MemoryRejected, MemoryStore
from lumi.memory.vectors import MemoryIndex
from lumi.storage.memory import EpisodeStore
from lumi.storage.retention import Deletion, RetentionService
from lumi.transport.methods import (
    METHOD_PANEL_MEMORY_CONFIRM,
    METHOD_PANEL_MEMORY_EDIT,
    METHOD_PANEL_MEMORY_ERASE,
    METHOD_PANEL_MEMORY_ERASE_PREVIEW,
    METHOD_PANEL_MEMORY_EXPORT,
    METHOD_PANEL_MEMORY_FORGET,
    METHOD_PANEL_MEMORY_SEARCH,
    METHOD_PANEL_SETTINGS_UPDATE,
)
from lumi.transport.payload import optional_str, require_str
from lumi.transport.server import RequestRefused, WsServer

log = lumi_logging.get_logger(__name__)

#: How many memories one page of the memory window holds. **A page, not everything**: the
#: window is a list a person reads, and a thousand rows in one frame is not readable.
PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

#: The longest text the window may put in one field. **Not a storage limit** — a memory is
#: one sentence, and something the length of an essay is a note the user wants somewhere
#: else. A filter that long is not a filter either, so the same bound holds for `query`.
MAX_CONTENT_CHARS: Final = 500

#: The confirmation token the window must echo back to erase everything.
#:
#: **Not a password.** It is there so that "erase" cannot be reached by a single
#: mistyped request: the caller has to have read the preview to know what to send.
ERASE_CONFIRMATION: Final = "erase-everything"


def record_payload(record: MemoryRecord, *, now: datetime) -> dict[str, Any]:
    """One memory as the window shows it.

    **`effective_salience` is computed here, not stored.** It is a function of the clock,
    and a number frozen at write time would tell the user how important this was months
    ago rather than how close it is to fading now.
    """
    return {
        "id": record.id,
        "type": record.type.value,
        "subject": record.subject,
        "content": record.content,
        "assertion_mode": record.assertion_mode.value,
        "confidence": record.confidence,
        "trust_level": record.trust_level.value,
        "provenance_class": record.provenance_class.value,
        "base_salience": record.base_salience,
        "effective_salience": decay.effective_salience(record, now),
        "created_at": record.created_at.isoformat(),
        "valid_from": record.valid_from.isoformat(),
        "last_accessed": record.last_accessed.isoformat(),
        "access_count": record.access_count,
        "archived": record.archived_at is not None,
        "superseded_by": record.superseded_by,
        "evidence": list(record.evidence_ref),
        "episodes": list(record.source_episode_ids),
    }


def deletion_payload(deletions: Sequence[Deletion]) -> list[dict[str, Any]]:
    """The per-target counts. **Zeroes included** — see docs/architecture/ui.md §5b."""
    return [{"target": deletion.target.value, "count": deletion.count} for deletion in deletions]


def _write_export(target: Path, document: dict[str, Any]) -> Path:
    """Serialise and write, on a thread. **Both halves, not just the write** — for a large
    memory `json.dumps` is the more expensive of the two.

    **Never onto a file that is already there.** The name is only accurate to the second,
    so two exports inside one second aim at the same path; opening with `x` rather than
    truncating means the second gets a file of its own instead of overwriting — or
    interleaving with — the copy the user was just told the path of. The name actually
    used is what comes back, because that is what gets shown.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, ensure_ascii=False, indent=2)
    candidate = target
    taken = 1
    while True:
        try:
            with candidate.open("x", encoding="utf-8") as handle:
                handle.write(text)
        except FileExistsError:
            taken += 1
            candidate = target.with_name(f"{target.stem}-{taken}{target.suffix}")
            continue
        return candidate


class PanelService:
    """Answers what the settings and memory windows ask.

    **The clock is injected**, like everywhere else that reads it: salience decays, and a
    view of it that can only be tested by waiting a fortnight is one that ships untested.
    """

    __slots__ = (
        "_browser",
        "_clock",
        "_episodes",
        "_index",
        "_retention",
        "_settings_update",
        "_store",
    )

    def __init__(
        self,
        *,
        store: MemoryStore,
        browser: MemoryBrowser,
        index: MemoryIndex,
        episodes: EpisodeStore,
        retention: RetentionService,
        settings_update: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        #: Paging for the window and the export. **Reads only** — the writes stay behind
        #: `MemoryStore`, which is the sole writer of `memories` (ADR-045)
        self._browser = browser
        self._index = index
        #: Only ever asked how much is waiting to be reflected on. **An empty memory list
        #: with unread conversation behind it is a different fact** from an empty one
        #: without, and the window cannot tell them apart on its own.
        self._episodes = episodes
        self._retention = retention
        #: The settings window asks for exactly what the character window may ask for.
        #: **The same handler**, reached under a different name because the namespace is
        #: what says which window asked.
        self._settings_update = settings_update
        self._clock = clock

    def register(self, server: WsServer) -> None:
        """Make the routes reachable. **Registration is the allowlist** (ADR-028)."""
        server.on_request(METHOD_PANEL_SETTINGS_UPDATE, self._settings_update)
        server.on_request(METHOD_PANEL_MEMORY_SEARCH, self.search)
        server.on_request(METHOD_PANEL_MEMORY_EDIT, self.edit)
        server.on_request(METHOD_PANEL_MEMORY_FORGET, self.forget)
        server.on_request(METHOD_PANEL_MEMORY_CONFIRM, self.confirm)
        server.on_request(METHOD_PANEL_MEMORY_EXPORT, self.export)
        server.on_request(METHOD_PANEL_MEMORY_ERASE_PREVIEW, self.erase_preview)
        server.on_request(METHOD_PANEL_MEMORY_ERASE, self.erase)

    async def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        """A page of memories, newest first, optionally filtered by substring."""
        raw_limit = payload.get("limit")
        limit = raw_limit if isinstance(raw_limit, int) and raw_limit > 0 else PAGE_SIZE
        raw_offset = payload.get("offset")
        offset = raw_offset if isinstance(raw_offset, int) and raw_offset > 0 else 0
        records, total = await self._browser.browse(
            # **A malformed filter is refused, not dropped.** `limit` and `offset` are
            # view mechanics with an obvious default, but `query` decides which of the
            # user's memories they are looking at — silently ignoring one that arrived as
            # a number would show everything and read as the search box having broken.
            query=optional_str(payload, "query", limit=MAX_CONTENT_CHARS) or "",
            # **Only a literal `true` opens the history.** `bool("false")` is `True`, so
            # what the window did not explicitly ask for is not shown.
            include_history=payload.get("include_history") is True,
            limit=min(limit, MAX_PAGE_SIZE),
            offset=offset,
        )
        now = self._clock()
        return {
            "items": [record_payload(record, now=now) for record in records],
            "total": total,
            "offset": offset,
            # **Why the list might be empty.** Reflection runs when the conversation goes
            # quiet, so "nothing here yet" is often "nothing read yet" — and a window that
            # cannot say which invites the conclusion that memory is broken.
            "pending_turns": await self._episodes.unreflected_turns(),
        }

    async def edit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Correct a memory. **Supersedes; the old wording stays readable.**"""
        memory_id = require_str(payload, "id")
        content = require_str(payload, "content", limit=MAX_CONTENT_CHARS)
        # **`None` keeps the subject it already has** — the window edits the sentence far
        # more often than the thing it is about, and has no reason to resend it.
        subject = optional_str(payload, "subject", limit=MAX_CONTENT_CHARS)
        try:
            record = await self._store.rewrite(
                memory_id, content=content, subject=subject, now=self._clock()
            )
        except MemoryRejected as refused:
            raise RequestRefused(str(refused)) from refused
        # **The old vector still points at the old sentence.** Dropping it here rather than
        # waiting for the next index pass means a search cannot return the text the user
        # just corrected — which would read as the correction not having been saved.
        await self._index.remove([memory_id])
        return {"memory": record_payload(record, now=self._clock())}

    async def confirm(self, payload: dict[str, Any]) -> dict[str, Any]:
        """ "This is right." **The only escalation to TRUSTED** (Invariant 7)."""
        try:
            record = await self._store.confirm(require_str(payload, "id"), now=self._clock())
        except MemoryRejected as refused:
            raise RequestRefused(str(refused)) from refused
        return {"memory": record_payload(record, now=self._clock())}

    async def forget(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Delete one memory outright.

        **The index entry goes first.** If the process dies between the two, an orphaned
        vector points at a row that no longer exists, and retrieval drops it. The other
        order leaves the memory's text in `memory_fts` after the memory is gone, which is
        the failure that matters: deleted, and still searchable.
        """
        memory_id = require_str(payload, "id")
        if await self._store.get(memory_id) is None:
            raise RequestRefused("no_such_memory")
        await self._index.remove([memory_id])
        deletion = await self._retention.purge_memories([memory_id], now=self._clock())
        return {"deleted": deletion.count}

    async def export(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write everything remembered to a file.

        **The output is plain text and the window says so before this is called.** Writing
        it encrypted would be worse, not better: an export nobody else can read is not an
        export, and pretending otherwise would leave the user believing a copy of their
        memory is protected when the point of it is to be portable.
        """
        del payload
        now = self._clock()
        # **Paged by cursor, not by offset.** Reflection writes memories while the window
        # is open, and an offset counted against a table that grew between two pages skips
        # a record or writes it out twice — in the one file that is supposed to be the
        # complete copy. Reading it all under one transaction would be consistent and
        # would also hold the database against every other write for the whole export.
        records: list[MemoryRecord] = []
        while True:
            page = await self._browser.everything_after(
                after=records[-1] if records else None, limit=MAX_PAGE_SIZE
            )
            records.extend(page)
            if len(page) < MAX_PAGE_SIZE:
                break

        target = paths.data_dir() / "exports" / f"lumi-memory-{now.strftime('%Y%m%d-%H%M%S')}.json"
        document = {
            "exported_at": now.isoformat(),
            "memories": [record_payload(record, now=now) for record in records],
        }
        # **Serialising and writing both go to a thread.** A long memory is megabytes of
        # JSON, and doing that on the event loop stalls audio — the one thing in Lumi with
        # a deadline. `asyncio.to_thread` is how every other blocking call here behaves.
        written = await asyncio.to_thread(_write_export, target, document)
        log.info("panel.memory.exported", count=len(records), path=str(written))
        return {"path": str(written), "count": len(records), "plaintext": True}

    async def erase_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """What "erase everything" would remove, per row of privacy.md §2."""
        del payload
        return {
            "targets": deletion_payload(await self._retention.count_everything()),
            "confirmation": ERASE_CONFIRMATION,
        }

    async def erase(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Erase everything. **Requires the token from the preview.**"""
        if payload.get("confirmation") != ERASE_CONFIRMATION:
            raise RequestRefused("confirmation_required")
        # **The indexes go with it, inside `erase_everything`.** `memory_fts` holds the
        # text of every memory, so a second call from out here would be a second place
        # that has to remember to empty it — and the one that forgets wins.
        deletions = await self._retention.erase_everything(now=self._clock())
        log.info("panel.memory.erased", targets=len(deletions))
        return {"targets": deletion_payload(deletions)}
