"""The coordinator for first-run setup. **All decisions are concentrated here**
(the Stage only displays).

Design → docs/architecture/setup.md

Flow:

```
Startup → detection (local only) → state is determined
Stage connects → state is broadcast
  If state is not_configured and it hasn't been asked yet → ask whether to fetch
    "Fetch" → installing (progress broadcast) → installed / failed
    "Don't fetch" → stays not_configured. **Only remembers that it was asked**
```
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from lumi import logging as lumi_logging
from lumi import paths
from lumi.setup.detect import detect_engines
from lumi.setup.engines import AIVISSPEECH_ENGINE
from lumi.setup.install import SetupError, install_engine
from lumi.setup.state import EngineRuntime, SetupAnswers, TtsSetup, TtsSetupState
from lumi.transport.protocol import Role
from lumi.transport.server import NotConnectedError, WsServer

log = lumi_logging.get_logger(__name__)

#: How long to wait for the user's choice. **Human time**, so it's long.
PROMPT_TIMEOUT_S = 600.0

#: The cap on how many times to ask (including retries after a failure). **Never nags.**
MAX_PROMPTS = 2

#: The method for broadcasting state (Core → Stage).
METHOD_STATE = "stage.setup.state"
#: The method for asking whether to fetch (Core → Stage, awaits the result).
METHOD_PROMPT = "stage.setup.prompt"

#: The choices the Stage returns in `result`. **A value that goes on the wire**, so
#: docs/contracts/wire.json is authoritative (→ ADR-022). `CHOICE_SKIP` isn't used in
#: any comparison, but it's kept here so **only one side of the contract isn't documented**.
CHOICE_INSTALL = "install"
CHOICE_SKIP = "skip"


class SetupCoordinator:
    def __init__(self, server: WsServer, env: Mapping[str, str]) -> None:
        self._server = server
        self._env = env
        self._state = TtsSetup(state=TtsSetupState.UNKNOWN)
        self._answers_path: Path = paths.setup_state_file()
        self._answers = SetupAnswers()
        # **Kept as two separate flags.** `_prompting` is "is this sequence
        # currently in progress" (prevents duplicate runs); `_awaiting_answer` is
        # "is a question currently shown on screen" (boot phase). Conflating them
        # made the question screen flash back right after fetching finished (hit
        # this in testing).
        self._prompting = False
        self._awaiting_answer = False
        # **The Stage can connect before detection finishes.** Observed connecting
        # first in practice (2026-08-15). Deciding whether to prompt while state is
        # still unknown would skip asking when it should.
        self._initialized = asyncio.Event()

    @property
    def state(self) -> TtsSetup:
        return self._state

    async def initialize(self) -> None:
        """Called exactly once at startup. **No external communication.**"""
        try:
            self._answers = await asyncio.to_thread(SetupAnswers.load, self._answers_path)
            await self._redetect()
        finally:
            # Even on failure, whatever is waiting is never left hanging.
            self._initialized.set()

    async def _redetect(self) -> None:
        engines = await detect_engines(self._env)
        usable = next((engine for engine in engines if engine.executable or engine.running), None)
        if usable is None:
            await self._set_state(TtsSetup(state=TtsSetupState.NOT_CONFIGURED))
            return
        # Distinguishes what Lumi installed from what the user installed themselves (setup.md §2).
        await self._set_state(
            TtsSetup(
                state=(
                    TtsSetupState.INSTALLED if usable.managed_by_lumi else TtsSetupState.DETECTED
                ),
                engine_name=usable.display_name,
                version=usable.version,
                port=usable.port,
                executable=str(usable.executable) if usable.executable else None,
            )
        )

    async def _set_state(self, state: TtsSetup) -> None:
        self._state = state
        await self._broadcast()

    async def _broadcast(self) -> None:
        """Broadcasts the current state. **Includes the boot phase** (docs/architecture/ui.md).

        The phase also depends on "is a question currently being asked," so
        **it's rebroadcast whenever asking starts / finishes**, even if the state
        itself hasn't changed.
        """
        payload = self._state.to_payload(prompting=self._awaiting_answer)
        log.info("setup.state", **payload)
        await self._server.notify(Role.STAGE, METHOD_STATE, payload)

    async def set_runtime(self, runtime: EngineRuntime) -> None:
        """The engine **process**'s state changed.

        A separate axis from installation state, but it's the same single state
        distributed to the Stage, so **broadcasting is consolidated to this one
        exit point** (sending from two places couldn't guarantee ordering).
        """
        await self._set_state(replace(self._state, runtime=runtime))

    async def on_stage_connected(self) -> None:
        """The Stage connected. Broadcasts the current state and asks if needed.

        **Waits for detection to finish.** The Stage's connection can arrive first,
        and without waiting, state would still be `unknown` and get judged as "no
        need to ask."
        """
        await self._initialized.wait()
        await self._broadcast()

        if self._state.state is not TtsSetupState.NOT_CONFIGURED:
            return
        if self._answers.tts_prompt_answered or self._prompting:
            # **Once answered, never asked again.** Asking every startup is the textbook definition of annoying.
            return

        self._prompting = True
        try:
            await self._ask_and_maybe_install()
        finally:
            self._prompting = False
            self._awaiting_answer = False
            # Broadcasts that answering is done (or was given up on). **Never left hanging.**
            await self._broadcast()

    async def _ask_and_maybe_install(self) -> None:
        """Asks, and fetches if chosen. **Asks again exactly once if it fails.**

        Only one retry. Beyond that, the annoyance outweighs the benefit.
        State stays `failed` since it's never reverted to "not yet attempted."
        """
        retry = False
        reason: str | None = None

        for _ in range(MAX_PROMPTS):
            # **Broadcasts the phase before showing the question.** Forgetting this
            # would leave the Stage showing a loading indicator while the question
            # sits hidden behind it.
            self._awaiting_answer = True
            await self._broadcast()
            try:
                result = await self._server.invoke(
                    Role.STAGE,
                    METHOD_PROMPT,
                    {"retry": retry, "reason": reason},
                    timeout=PROMPT_TIMEOUT_S,
                )
            except (NotConnectedError, TimeoutError):
                # No answer was received. **Never counted as "asked"** (asked again next startup).
                log.info("setup.prompt.unanswered")
                return

            # **The question disappears the moment an answer arrives.** Never lets the question paint over the fetching phase.
            self._awaiting_answer = False
            choice = result.payload.get("choice") if result.ok else None
            # **An unrecognized answer is treated the same as "don't fetch"** (fail-closed).
            install = choice == CHOICE_INSTALL
            log.info("setup.prompt.answered", choice=choice, install=install)

            self._answers = SetupAnswers(tts_prompt_answered=True)
            await asyncio.to_thread(self._answers.save, self._answers_path)

            if not install:
                return

            await self.install_tts_engine()
            if self._state.state is not TtsSetupState.FAILED:
                return

            retry = True
            reason = self._state.reason

    async def install_tts_engine(self) -> None:
        """Fetches because the user chose to. **No external communication happens before this point.**"""
        artifact = AIVISSPEECH_ENGINE
        await self._set_state(
            TtsSetup(
                state=TtsSetupState.INSTALLING,
                engine_name=artifact.display_name,
                version=artifact.version,
                progress=0.0,
            )
        )

        last_sent = -1.0

        async def report(fraction: float) -> None:
            nonlocal last_sent
            # Broadcasts every 1%. Sending on every chunk would flood the WS with progress updates.
            if fraction - last_sent < 0.01 and fraction < 1.0:
                return
            last_sent = fraction
            # Only progress is updated. **`_state` stays INSTALLING**, so the phase
            # stays installing too.
            #
            # **Broadcasting always goes through `_set_state`.** Calling `notify`
            # directly here would mean choosing the flag passed to
            # `to_payload(prompting=...)` by hand, and risk conflating `_prompting`
            # (is this sequence in progress) with `_awaiting_answer` (is a question
            # currently shown). Conflating them would stream `boot=setup` for the
            # entire 200MB fetch with no progress shown.
            await self._set_state(replace(self._state, progress=fraction))

        try:
            executable = await install_engine(artifact, paths.engines_dir(), progress=report)
        except SetupError as error:
            log.warning("setup.install.failed", reason=error.reason, detail=error.detail)
            await self._set_state(
                TtsSetup(
                    state=TtsSetupState.FAILED,
                    engine_name=artifact.display_name,
                    version=artifact.version,
                    reason=error.reason,
                )
            )
            return
        except asyncio.CancelledError:
            await self._set_state(TtsSetup(state=TtsSetupState.FAILED, reason="cancelled"))
            raise
        except Exception as error:
            # Even for the unexpected, **never silently reverts to not-configured.** What happened is recorded.
            log.exception("setup.install.crashed")
            await self._set_state(TtsSetup(state=TtsSetupState.FAILED, reason="unexpected_error"))
            del error
            return

        await self._set_state(
            TtsSetup(
                state=TtsSetupState.INSTALLED,
                engine_name=artifact.display_name,
                version=artifact.version,
                port=artifact.default_port,
                executable=str(executable),
            )
        )
