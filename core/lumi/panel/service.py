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

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from lumi import logging as lumi_logging
from lumi import paths
from lumi.memory import decay
from lumi.memory.records import MemoryRecord
from lumi.memory.store import MemoryRejected, MemoryStore
from lumi.memory.vectors import MemoryIndex
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
from lumi.transport.server import RequestRefused, WsServer

log = lumi_logging.get_logger(__name__)

#: How many memories one page of the memory window holds. **A page, not everything**: the
#: window is a list a person reads, and a thousand rows in one frame is not readable.
PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

#: The longest correction accepted. **Not a storage limit** — a memory is one sentence,
#: and something the length of an essay is a note the user wants somewhere else.
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


def _text(payload: dict[str, Any], key: str, *, limit: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RequestRefused(f"{key}_required")
    text = value.strip()
    if not text:
        raise RequestRefused(f"{key}_required")
    if len(text) > limit:
        raise RequestRefused(f"{key}_too_long")
    return text


def _memory_id(payload: dict[str, Any]) -> str:
    value = payload.get("id")
    if not isinstance(value, str) or not value:
        raise RequestRefused("id_required")
    return value


class PanelService:
    """Answers what the settings and memory windows ask.

    **The clock is injected**, like everywhere else that reads it: salience decays, and a
    view of it that can only be tested by waiting a fortnight is one that ships untested.
    """

    __slots__ = ("_clock", "_index", "_retention", "_settings_update", "_store")

    def __init__(
        self,
        *,
        store: MemoryStore,
        index: MemoryIndex,
        retention: RetentionService,
        settings_update: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._index = index
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
        query = payload.get("query")
        records, total = await self._store.browse(
            query=query if isinstance(query, str) else "",
            include_history=bool(payload.get("include_history")),
            limit=min(limit, MAX_PAGE_SIZE),
            offset=offset,
        )
        now = self._clock()
        return {
            "items": [record_payload(record, now=now) for record in records],
            "total": total,
            "offset": offset,
        }

    async def edit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Correct a memory. **Supersedes; the old wording stays readable.**"""
        memory_id = _memory_id(payload)
        content = _text(payload, "content", limit=MAX_CONTENT_CHARS)
        subject = payload.get("subject")
        try:
            record = await self._store.rewrite(
                memory_id,
                content=content,
                subject=subject.strip() if isinstance(subject, str) and subject.strip() else None,
                now=self._clock(),
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
            record = await self._store.confirm(_memory_id(payload), now=self._clock())
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
        memory_id = _memory_id(payload)
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
        records: list[MemoryRecord] = []
        offset = 0
        while True:
            page, total = await self._store.browse(
                include_history=True, limit=MAX_PAGE_SIZE, offset=offset
            )
            records.extend(page)
            offset += len(page)
            if not page or offset >= total:
                break

        directory = paths.data_dir() / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"lumi-memory-{now.strftime('%Y%m%d-%H%M%S')}.json"
        target.write_text(
            json.dumps(
                {
                    "exported_at": now.isoformat(),
                    "memories": [record_payload(record, now=now) for record in records],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info("panel.memory.exported", count=len(records), path=str(target))
        return {"path": str(target), "count": len(records), "plaintext": True}

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
