"""Asking whether to fetch what is missing, and doing it if so.

Design → docs/architecture/setup.md §3
Decision → docs/decisions/ADR-019-tts-engine-distribution.md
Decision → docs/decisions/ADR-034-gate-startup-on-complete-setup.md

**One question with the total, before any question with a part.** Four consecutive
prompts each carrying its own size make the number that actually matters — what this
will cost altogether — the one number nobody is ever shown (roadmap 2g).

## The two flags are not the same flag

`_prompting` is "this sequence is running", and stops a second Stage connection starting
a second one. The broadcaster's `asking` is "a question is on screen", and decides the
boot phase. Conflating them made the question screen flash back the instant a fetch
finished.

## Declining is an answer; nobody answering is not

A decline holds for the session — following it with four more questions asks the same
thing four more times, which is how a consent prompt becomes something people dismiss
without reading. **An unanswered question is not held**: the next start asks again,
because nothing was decided.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from lumi import logging as lumi_logging
from lumi.artifacts.engines import AIVISSPEECH_ENGINE
from lumi.artifacts.models import HARRIER_OSS_V1_270M
from lumi.setup.acquire import Acquisition
from lumi.setup.broadcast import SetupStateBroadcaster
from lumi.setup.detection import selected_stt_artifact
from lumi.setup.llm_model import LlmModelChooser
from lumi.setup.ollama import OLLAMA_MODELS, QWEN_35_9B, OllamaModelArtifact
from lumi.setup.prompter import Prompter
from lumi.setup.state import (
    EmbeddingSetupState,
    LlmSetupState,
    SttSetupState,
    TtsSetupState,
)
from lumi.transport.methods import (
    CHOICE_INDIVIDUALLY,
    CHOICE_INSTALL,
    COMPONENT_ALL,
    COMPONENT_EMBEDDING,
    COMPONENT_LLM_MODEL,
    COMPONENT_STT,
    COMPONENT_TTS,
)

log = lumi_logging.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MissingComponent:
    """One thing that is not on this machine yet, and what fetching it would cost."""

    component: str
    #: What to call it on screen. **Not translated** — it is a product name (AivisSpeech,
    #: `harrier-oss-v1-270m`), and translating a name makes it unsearchable.
    name: str
    size_bytes: int

    def to_payload(self) -> dict[str, object]:
        return {"component": self.component, "name": self.name, "size_bytes": self.size_bytes}


class BulkAnswer(StrEnum):
    """What the user said to "fetch everything missing?".

    **`DECLINED` and `UNANSWERED` are different.** One is a decision to keep for this
    session; the other is nobody being there, which means asking again next start.
    """

    ALL = "all"
    INDIVIDUALLY = "individually"
    DECLINED = "declined"
    UNANSWERED = "unanswered"


class ConsentPrompter:
    """Runs the first-run question sequence. **Knows nothing about WebSockets.**"""

    __slots__ = ("_acquire", "_env", "_llm_model", "_prompter", "_prompting", "_ready", "_state")

    def __init__(
        self,
        state: SetupStateBroadcaster,
        prompter: Prompter,
        acquire: Acquisition,
        llm_model: LlmModelChooser,
        env: Mapping[str, str],
        *,
        ready: asyncio.Event,
    ) -> None:
        self._state = state
        self._prompter = prompter
        self._acquire = acquire
        self._llm_model = llm_model
        self._env = env
        #: Set once detection has finished. **The Stage can connect first** (observed
        #: 2026-08-15), and judging "nothing to ask" against unknown state skips asking.
        self._ready = ready
        self._prompting = False

    async def run(self) -> None:
        """The Stage connected. Broadcasts the current state and asks if needed.

        **Waits for detection to finish.** The Stage's connection can arrive first,
        and without waiting, state would still be `unknown` and get judged as "no
        need to ask."
        """
        await self._ready.wait()
        await self._state.publish()

        if self._prompting:
            return
        self._prompting = True
        try:
            # **One question before the per-component ones.** Being asked four times in a
            # row, each with its own size, makes the total impossible to see — and the
            # total is the number someone actually decides on.
            answer = await self._ask_everything()
            if answer is BulkAnswer.UNANSWERED:
                return
            if answer is BulkAnswer.DECLINED:
                # "Not now" for the whole set. **Asking the same thing again per component
                # would make the first answer meaningless.**
                return
            if answer is BulkAnswer.ALL:
                await self._install_everything()
                return
            if await self._tts():
                return
            if await self._stt():
                return
            await self._embedding()
        finally:
            self._prompting = False
            self._state.asking(False)
            # Broadcasts that answering is done (or was given up on). **Never left hanging.**
            await self._state.publish()

    async def _tts(self) -> bool:
        if self._state.snapshot.tts.state is not TtsSetupState.NOT_CONFIGURED:
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_TTS,
            install=self._acquire.tts_engine,
            failed=lambda: self._state.snapshot.tts.state is TtsSetupState.FAILED,
            reason=lambda: self._state.snapshot.tts.reason,
        )

    async def _stt(self) -> bool:
        if self._state.snapshot.stt.state is not SttSetupState.NOT_CONFIGURED:
            return False
        if self._state.snapshot.stt.model is None:
            # The selected name is not pinned, so there is nothing this could fetch.
            # **A question with no good answer is worse than no question**
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_STT,
            install=self._acquire.speech_model,
            failed=lambda: self._state.snapshot.stt.state is SttSetupState.FAILED,
            reason=lambda: self._state.snapshot.stt.reason,
        )

    async def _embedding(self) -> bool:
        """The embedding model (ADR-041). **Asked last, and never blocking.**

        Declining it costs similarity search and nothing else, so this is the one setup
        question whose "not now" leaves Lumi fully usable — which is also why it is asked
        after the three that do not.
        """
        if self._state.snapshot.embedding.state is not EmbeddingSetupState.NOT_CONFIGURED:
            return False
        return await self._ask_and_maybe_install(
            component=COMPONENT_EMBEDDING,
            install=self._acquire.embedding_model,
            failed=lambda: self._state.snapshot.embedding.state is EmbeddingSetupState.FAILED,
            reason=lambda: self._state.snapshot.embedding.reason,
        )

    def _missing(self) -> list[MissingComponent]:
        """What is not on this machine yet, with what each would cost to fetch.

        **Ollama itself is never in here.** Lumi does not install it (ADR-023), so listing
        it under a button that fetches things would promise something this cannot do.
        """
        missing: list[MissingComponent] = []
        if self._state.snapshot.tts.state is TtsSetupState.NOT_CONFIGURED:
            missing.append(
                MissingComponent(
                    component=COMPONENT_TTS,
                    name=AIVISSPEECH_ENGINE.display_name,
                    size_bytes=AIVISSPEECH_ENGINE.size,
                )
            )
        stt_artifact = selected_stt_artifact(self._env)
        if (
            self._state.snapshot.stt.state is SttSetupState.NOT_CONFIGURED
            and stt_artifact is not None
        ):
            missing.append(
                MissingComponent(
                    component=COMPONENT_STT,
                    name=stt_artifact.name,
                    size_bytes=stt_artifact.size,
                )
            )
        # **Only when Ollama is there to fetch it into.** Offering to download a 6.6 GB
        # model that nothing can receive is worse than not offering.
        # **One lookup, and no `assert` to narrow it.** `assert` disappears under `-O`,
        # which would turn a missing model into an `AttributeError` in the one build where
        # nobody is watching.
        recommended = self._recommended()
        if (
            self._state.snapshot.llm.state is LlmSetupState.MODEL_MISSING
            and recommended is not None
        ):
            missing.append(
                MissingComponent(
                    component=COMPONENT_LLM_MODEL,
                    name=recommended.display_name,
                    size_bytes=recommended.size_bytes,
                )
            )
        if self._state.snapshot.embedding.state is EmbeddingSetupState.NOT_CONFIGURED:
            missing.append(
                MissingComponent(
                    component=COMPONENT_EMBEDDING,
                    name=HARRIER_OSS_V1_270M.name,
                    size_bytes=HARRIER_OSS_V1_270M.size,
                )
            )
        return missing

    def _recommended(self) -> OllamaModelArtifact | None:
        return OLLAMA_MODELS.get(self._state.snapshot.llm.model or "", QWEN_35_9B)

    async def _ask_everything(self) -> BulkAnswer:
        """Offers to fetch everything missing at once, with the total.

        **Skipped when there is nothing to fetch**, rather than asked and answered with an
        empty list — a consent dialog for zero bytes teaches people to dismiss consent
        dialogs.

        The answer is one of three, and "individually" is not the same as "no": it means
        the user wants to decide per item, which is what the existing per-component
        questions are for.
        """
        missing = self._missing()
        if not missing:
            return BulkAnswer.INDIVIDUALLY
        result = await self._prompter.ask(
            {
                "component": COMPONENT_ALL,
                "retry": False,
                "reason": None,
                "items": [item.to_payload() for item in missing],
                "total_bytes": sum(item.size_bytes for item in missing),
            }
        )
        if result is None:
            log.info("setup.prompt.unanswered", component=COMPONENT_ALL)
            return BulkAnswer.UNANSWERED

        choice = result.payload.get("choice") if result.ok else None
        log.info("setup.prompt.answered", component=COMPONENT_ALL, choice=choice)
        if choice == CHOICE_INSTALL:
            return BulkAnswer.ALL
        if choice == CHOICE_INDIVIDUALLY:
            return BulkAnswer.INDIVIDUALLY
        # **Anything unrecognised is "not now"** (fail-closed): nothing is fetched.
        return BulkAnswer.DECLINED

    async def _install_everything(self) -> None:
        """Fetches everything the bulk question listed, in order.

        **A failure does not stop the rest.** Each component reports its own state, and
        abandoning the remaining three because one distributor had a bad minute would turn
        one retry into four.
        """
        for item in self._missing():
            component = item.component
            if component == COMPONENT_TTS:
                await self._acquire.tts_engine()
            elif component == COMPONENT_STT:
                await self._acquire.speech_model()
            elif component == COMPONENT_LLM_MODEL:
                recommended = self._recommended()
                if recommended is not None:
                    await self._llm_model.pull(recommended)
            elif component == COMPONENT_EMBEDDING:
                await self._acquire.embedding_model()

    async def _ask_and_maybe_install(
        self,
        *,
        component: str,
        install: Callable[[], Awaitable[None]],
        failed: Callable[[], bool],
        reason: Callable[[], str | None],
    ) -> bool:
        """Asks, and fetches if chosen. **After a failure, offers the choice again.**

        The loop only turns when the user chose to fetch *and* the fetch failed, so
        **every repetition is one the user asked for** — which is why there is no cap on
        it (ADR-034). Most fetch failures are transient (a dropped connection, the
        distributor having a bad minute), and without a retry the only way to use a
        connection that already came back is to restart Lumi.

        "Not now" ends it here. The state stays `not_configured` / `failed` — **never
        reverted to "not yet attempted"** — and the boot phase becomes `blocked`, which
        is what puts the missing pieces and their fixes on screen.

        Returns `True` when the prompt could not be answered, so the caller can stop the
        component sequence. A user answer, including "not now", returns `False`.
        """
        retry = False
        detail: str | None = None

        while True:
            # **Broadcasts the phase before showing the question.** Forgetting this
            # would leave the Stage showing a loading indicator while the question
            # sits hidden behind it.
            result = await self._prompter.ask(
                {"component": component, "retry": retry, "reason": detail}
            )
            if result is None:
                # Nobody is there to answer. **Stop asking** — the next start asks again.
                log.info("setup.prompt.unanswered", component=component)
                return True

            choice = result.payload.get("choice") if result.ok else None
            # **An unrecognized answer is treated the same as "not now"** (fail-closed).
            chose_install = choice == CHOICE_INSTALL
            log.info(
                "setup.prompt.answered", component=component, choice=choice, install=chose_install
            )

            if not chose_install:
                return False

            await install()
            if not failed():
                return False

            # **A failure is never smoothed over into "done".** It goes back to the user
            # with its reason and the same two choices.
            retry = True
            detail = reason()
