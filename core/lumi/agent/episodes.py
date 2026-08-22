"""Writing the conversation down. **The first thing Lumi keeps after the process exits.**

Design → docs/architecture/memory.md / Privacy → docs/contracts/privacy.md §2 (row 1)

Working Memory holds the conversation while it is happening; this holds it afterwards.
The two record the same turns, and **this one mirrors what Working Memory accepted**
rather than deciding for itself — a log that disagrees with what Lumi believes it said
is worse than no log.

## Why the write does not happen inline

The user's utterance is recorded before the LLM is asked anything, so an `await` there
would put a disk write on the turn's critical path (docs/architecture/audio.md §7). The
write is handed to a task instead, and the turn carries on.

**The writes are serialized among themselves.** `turn_index` is assigned synchronously
when the turn happens, so the log reads back in the order it was spoken; a lock keeps the
writes themselves in that order too, because **an utterance cannot be written before the
episode that contains it exists.**

## Failure is loud, and costs the log rather than the conversation

If the database cannot be written, **the turn still completes.** Lumi failing to answer
because it could not file the answer away would be the wrong trade. It is logged as a
warning, never swallowed.

## An episode is opened by the first thing said

A session where nobody spoke leaves nothing behind — not an empty episode with a
timestamp, which would be a record of a conversation that did not happen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from uuid import uuid4

from lumi import logging as lumi_logging
from lumi.provenance import ProvenanceClass, TrustLevel
from lumi.storage.memory import SPEAKER_LUMI, SPEAKER_USER, Episode, EpisodeStore, Utterance

log = lumi_logging.get_logger(__name__)


class EpisodeRecorder:
    """One session's worth of conversation, on its way to disk."""

    __slots__ = (
        "_clock",
        "_episode_id",
        "_order",
        "_session_id",
        "_store",
        "_turn_index",
        "_writes",
    )

    def __init__(
        self,
        store: EpisodeStore,
        *,
        session_id: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._session_id = session_id or uuid4().hex
        self._clock = clock
        self._episode_id: str | None = None
        self._turn_index = 0
        self._writes: set[asyncio.Task[None]] = set()
        #: **Writes run one at a time, in the order the turns happened.** Without it the
        #: second utterance can reach the database before the episode it belongs to has
        #: been created, and the foreign key throws it away — the user said something and
        #: nothing kept it. Tasks acquire this in creation order, which is turn order.
        self._order = asyncio.Lock()

    @property
    def episode_id(self) -> str | None:
        """`None` until something has actually been said."""
        return self._episode_id

    def remember_user(
        self, text: str, trust_level: TrustLevel, *, correlation_id: str | None = None
    ) -> None:
        """What the user said, **carrying the trust the Session granted it.**

        The level is passed in rather than restated here. `Session.record_user_utterance`
        is *the* direct user-input handler (docs/contracts/provenance.md), and a second
        module deciding the same thing would be a second place to audit — one that could
        drift into disagreeing with what Lumi actually acted on.

        Trust means "it came in through the user's own microphone or keyboard". It does
        **not** mean the voice was the user's: STT transcribes whoever is in the room, and
        privacy.md §6 says so out loud rather than implying an identity check that does
        not exist.
        """
        self._remember(SPEAKER_USER, text, trust_level, correlation_id)

    def remember_lumi(
        self, text: str, trust_level: TrustLevel, *, correlation_id: str | None = None
    ) -> None:
        """What Lumi said, carrying **the join of what went into producing it.**

        Stored rather than recomputed later: a Reflection Job next week cannot work out
        that a reply was shaped by an untrusted web page, and **no automatic step may
        raise trust** (Invariant 7). The record has to travel with the text.
        """
        self._remember(SPEAKER_LUMI, text, trust_level, correlation_id)

    def _remember(
        self, speaker: str, text: str, trust_level: TrustLevel, correlation_id: str | None
    ) -> None:
        if not text:
            return
        now = self._clock()
        opening: Episode | None = None
        if self._episode_id is None:
            self._episode_id = uuid4().hex
            opening = Episode(id=self._episode_id, session_id=self._session_id, started_at=now)
        utterance = Utterance(
            id=uuid4().hex,
            episode_id=self._episode_id,
            turn_index=self._turn_index,
            speaker=speaker,
            text=text,
            provenance_class=(
                ProvenanceClass.TRUSTED
                if trust_level is TrustLevel.TRUSTED
                else ProvenanceClass.DERIVED
            ),
            trust_level=trust_level,
            occurred_at=now,
            correlation_id=correlation_id,
        )
        self._turn_index += 1
        self._spawn(self._write(opening, utterance))

    async def _write(self, opening: Episode | None, utterance: Utterance) -> None:
        async with self._order:
            try:
                if opening is not None:
                    await self._store.open_episode(opening)
                await self._store.append(utterance)
            except Exception as error:
                # **The conversation is not cancelled because the filing cabinet is stuck.**
                log.warning("episode.write_failed", error=str(error), speaker=utterance.speaker)

    def _spawn(self, coroutine: Coroutine[None, None, None]) -> None:
        """**Tracked, not fired and forgotten.** `flush` is what makes a test able to read
        back what a turn wrote, and an untracked task is one nobody can wait for.
        """
        task = asyncio.create_task(coroutine, name="episode-write")
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def flush(self) -> None:
        """Wait for the writes started so far. **Called before anything reads the log.**"""
        if self._writes:
            await asyncio.wait(set(self._writes))

    async def close(self) -> None:
        """End the episode. **Flushes first** — an episode closed before its last line was
        written would have an `ended_at` earlier than something inside it.
        """
        await self.flush()
        if self._episode_id is None:
            return
        try:
            await self._store.close_episode(self._episode_id, self._clock())
        except Exception as error:
            log.warning("episode.close_failed", error=str(error))
