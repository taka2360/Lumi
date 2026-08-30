"""What Core tells the windows. **One place, so the order is one order.**

Design → docs/architecture/ui.md §5b
Decision → docs/decisions/ADR-042-panel-windows-and-panel-role.md

Three things are pushed rather than asked for: the settings, whether the microphone is
open, and which character model to draw. They go to different windows and they are sent
from different moments in the runtime's life, which is how sending them acquired three
different spellings of "notify the Stage".

**Core sends codes, never sentences** (ADR-036). Nothing assembled here is a display
string; the Stage owns the locale and everything the user reads.

## Sent after the change, never on the request

The microphone indicator is the only thing telling someone whether the room is being
listened to. An indicator that goes dark on the press rather than on the change is
lying whenever the change fails, which is precisely when it matters.
"""

from __future__ import annotations

from collections.abc import Callable

from lumi import logging as lumi_logging
from lumi.audio.io import AudioIO
from lumi.content.pack import CharacterPack
from lumi.settings import Settings
from lumi.transport.methods import (
    METHOD_MIC,
    METHOD_MODEL,
    METHOD_PANEL_SETTINGS,
    METHOD_SETTINGS,
    REASON_MODEL_NOT_IN_PACK,
    REASON_PACK_UNREADABLE,
)
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)


class WindowAnnouncer:
    """Pushes state to the character window and the panels."""

    __slots__ = ("_audio", "_listening", "_pack", "_server", "_settings")

    def __init__(
        self,
        server: WsServer,
        *,
        settings: Callable[[], Settings],
        audio: AudioIO,
        pack: CharacterPack | None,
        listening: Callable[[], bool],
    ) -> None:
        self._server = server
        #: **Read at send time.** Both of these are rebound while Lumi runs — settings by
        #: the settings window, listening by the audio stack coming up — and a snapshot
        #: would announce the state at construction for the rest of the session.
        self._settings = settings
        self._listening = listening
        self._audio = audio
        self._pack = pack

    async def settings(self) -> None:
        """Send the settings snapshot to **both** the character window and the panels.

        The settings window is where they are read and changed; the character window
        needs `locale`, which decides the language of everything it draws. Sending to one
        and not the other would leave the bubble in the language the app started in
        (ADR-042).
        """
        payload = self._settings().to_payload()
        await self._server.notify(Role.STAGE, METHOD_SETTINGS, payload)
        await self._server.notify(Role.PANEL, METHOD_PANEL_SETTINGS, payload)

    async def microphone(self) -> None:
        """Whether the microphone is open, and whether the user muted it (ui.md §5b).

        **Open means a stream is actually being read**, so a muted microphone is not open:
        muting closes it (`AudioIO.set_input_muted`).
        """
        await self._server.notify(
            Role.STAGE,
            METHOD_MIC,
            {
                "open": (
                    self._listening() and self._audio.can_listen and not self._audio.input_muted
                ),
                "muted": self._audio.input_muted,
            },
        )

    async def character_model(self) -> None:
        """Tells the Stage **which model to draw, and the credit that goes with it** (ADR-029).

        **Sent even when there is no model** (`path: null`). "Nothing has arrived yet" and
        "this Content Pack ships no model" are different states, and only the second one
        should put the placeholder on screen with a reason (docs/architecture/ui.md).

        An absolute path, not a URL — **Core does not serve files** and does not know how
        Shell addresses them. The reason is a code for the same kind of reason: **Core
        does not render, either** (ADR-036).
        """
        model = self._pack.model if self._pack else None
        if model is None:
            # **A code, not a sentence** (ADR-036). Core does not know the Stage's locale,
            # and a display string sent from here would be the one line on screen that
            # switching language never reaches.
            reason = REASON_MODEL_NOT_IN_PACK if self._pack else REASON_PACK_UNREADABLE
            await self._server.notify(Role.STAGE, METHOD_MODEL, {"path": None, "reason": reason})
            log.info("character.model.absent", reason=reason)
            return
        await self._server.notify(Role.STAGE, METHOD_MODEL, model.to_payload())
        log.info("character.model", path=str(model.path), format=model.format)
