"""The setup state the Stage is looking at. **One owner, one exit point.**

Design → docs/architecture/setup.md §2 / docs/architecture/ui.md (boot phases) /
docs/decisions/ADR-034-gate-startup-on-complete-setup.md

Detection, the consent sequence and fetching all move this state, and each of them
would be a plausible place to send it from. **Sending from more than one place cannot
guarantee ordering**, and the thing being ordered is a progress bar and a question:
out of order, the Stage shows a finished fetch behind a screen still asking whether to
start it.

So they all hand their result here, and here is the only thing that talks to the Stage
about setup.

## Why the phase is derived rather than stored

`boot_phase` is a pure function of the snapshot plus "is a question on screen"
(ADR-034). Storing the phase would let it drift from the state it summarises, and the
runtime acts on one while the user sees the other. `boot` and `publish` call the same
function, so they cannot disagree.

**"A question is on screen" is not part of the snapshot**, which is why asking and
answering rebroadcast even when nothing about the components changed.
"""

from __future__ import annotations

from lumi import logging as lumi_logging
from lumi.setup.state import (
    BootPhase,
    EmbeddingSetup,
    LlmSetup,
    SetupSnapshot,
    SttSetup,
    TtsSetup,
    boot_phase,
)
from lumi.transport.methods import METHOD_SETUP_STATE
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)


class SetupStateBroadcaster:
    """Holds the snapshot, and is the only thing that sends it."""

    __slots__ = ("_awaiting_answer", "_server", "_snapshot")

    def __init__(self, server: WsServer) -> None:
        self._server = server
        self._snapshot = SetupSnapshot()
        #: **Not part of the snapshot.** Whether a question is currently shown changes the
        #: phase without changing any component, so it is held beside the state rather
        #: than in it.
        self._awaiting_answer = False

    @property
    def snapshot(self) -> SetupSnapshot:
        return self._snapshot

    @property
    def boot(self) -> BootPhase:
        """The phase the Stage is currently being shown. **The same derivation, not a
        second one** — this and `publish` read the one pure function, so what the runtime
        acts on and what the user sees can never disagree (ADR-034).
        """
        return boot_phase(self._snapshot, prompting=self._awaiting_answer)

    @property
    def awaiting_answer(self) -> bool:
        return self._awaiting_answer

    def asking(self, value: bool) -> None:
        """Records that a question went up or came down. **Does not send.**

        Separate from `publish` because the two are not always paired: a question that
        ends in a fetch has its answer recorded and then the fetch's own state change
        does the sending, and pairing them here would put an extra snapshot between the
        answer and the progress bar.
        """
        self._awaiting_answer = value

    async def publish(self) -> None:
        """Sends the current state, **including the derived boot phase.**"""
        payload = self._snapshot.to_payload(prompting=self._awaiting_answer)
        log.info("setup.state", **payload)
        await self._server.notify(Role.STAGE, METHOD_SETUP_STATE, payload)

    async def replace(
        self,
        *,
        tts: TtsSetup | None = None,
        llm: LlmSetup | None = None,
        stt: SttSetup | None = None,
        embedding: EmbeddingSetup | None = None,
    ) -> None:
        """Replaces one component and sends. **The single exit point for state changes.**"""
        self._snapshot = SetupSnapshot(
            tts=tts if tts is not None else self._snapshot.tts,
            llm=llm if llm is not None else self._snapshot.llm,
            stt=stt if stt is not None else self._snapshot.stt,
            embedding=embedding if embedding is not None else self._snapshot.embedding,
        )
        await self.publish()

    async def replace_all(self, snapshot: SetupSnapshot) -> None:
        """Replaces every component at once and sends. **Detection's exit point** — it
        looks at all four and has no reason to send four times.
        """
        self._snapshot = snapshot
        await self.publish()
