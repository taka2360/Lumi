"""Fetching what the user chose to fetch. **Nothing here runs unanswered.**

Design → docs/architecture/setup.md §4
Decision → docs/decisions/ADR-019-tts-engine-distribution.md

Three components are fetched the same way — an engine, the speech model, the embedding
model — and differ only in which state object they build. How a fetch can end does not
differ at all, which is why `_run` exists and why it is one copy.

**Progress and failure both go out as setup state**, never as their own message. The
Stage draws one screen, and a progress bar that arrives on a different channel from the
state it belongs to is a progress bar that can be shown next to "not configured".
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import TypeVar

from lumi import logging as lumi_logging
from lumi import paths
from lumi.artifacts.engines import AIVISSPEECH_ENGINE
from lumi.artifacts.install import SetupError, install_engine, install_model
from lumi.artifacts.models import HARRIER_OSS_V1_270M
from lumi.setup.broadcast import SetupStateBroadcaster
from lumi.setup.detection import selected_stt_artifact
from lumi.setup.progress import throttle
from lumi.setup.state import (
    EmbeddingSetup,
    EmbeddingSetupState,
    SttSetup,
    SttSetupState,
    TtsSetup,
    TtsSetupState,
)

log = lumi_logging.get_logger(__name__)

#: What a fetch hands back. The engine returns its executable's path; the models return
#: nothing. **`_run` never looks at it** — it only hands it back.
T = TypeVar("T")


class Acquisition:
    """Fetches a component and reports how it went, through the setup state."""

    __slots__ = ("_env", "_state")

    def __init__(self, state: SetupStateBroadcaster, env: Mapping[str, str]) -> None:
        self._state = state
        self._env = env

    async def _run(
        self,
        *,
        event: str,
        run: Callable[[], Awaitable[T]],
        fail: Callable[[str], Awaitable[None]],
    ) -> T | None:
        """Runs one fetch. **Returns `None` when it failed**, having already broadcast why.

        The engine and the speech model differ only in which state object they build; how a
        fetch can end is the same for both, and was written out twice.

        | outcome | what happens |
        |---|---|
        | `SetupError` | `fail(reason)` — the reason is what the panel shows |
        | `CancelledError` | `fail("cancelled")`, **then re-raised** |
        | anything else | `fail("unexpected_error")`, logged with a traceback |
        | returned | the value is handed back |

        **The `CancelledError` branch is why this exists.** Dropping the `raise` from one of
        the two copies would swallow a cancellation during shutdown, and neither the type
        checker nor the tests would say a word. Now there is one copy to get right.

        **Never reverts to "not yet attempted."** A failure that read as `not_configured`
        would put the user back in front of the same question with no sign anything happened.
        """
        try:
            return await run()
        except SetupError as error:
            log.warning(f"{event}.failed", reason=error.reason, detail=error.detail)
            await fail(error.reason)
        except asyncio.CancelledError:
            # **Broadcast first, then re-raise.** Shutdown still has to unwind; what it must
            # not do is leave the Stage showing a fetch that is no longer running.
            await fail("cancelled")
            raise
        except Exception:
            # Even for the unexpected, **what happened is recorded.**
            log.exception(f"{event}.crashed")
            await fail("unexpected_error")
        return None

    async def tts_engine(self) -> None:
        """Fetches because the user chose to. **No external communication happens before this
        point.**
        """
        artifact = AIVISSPEECH_ENGINE
        await self._state.replace(
            tts=TtsSetup(
                state=TtsSetupState.INSTALLING,
                engine_name=artifact.display_name,
                version=artifact.version,
                progress=0.0,
            )
        )

        async def progress(fraction: float) -> None:
            await self._state.replace(tts=replace(self._state.snapshot.tts, progress=fraction))

        async def fail(reason: str) -> None:
            await self._state.replace(
                tts=TtsSetup(
                    state=TtsSetupState.FAILED,
                    engine_name=artifact.display_name,
                    version=artifact.version,
                    reason=reason,
                )
            )

        executable = await self._run(
            event="setup.install",
            run=lambda: install_engine(artifact, paths.engines_dir(), progress=throttle(progress)),
            fail=fail,
        )
        if executable is None:
            return

        await self._state.replace(
            tts=TtsSetup(
                state=TtsSetupState.INSTALLED,
                engine_name=artifact.display_name,
                version=artifact.version,
                port=artifact.default_port,
                executable=str(executable),
            )
        )

    async def embedding_model(self) -> None:
        """Fetches the embedding model (ADR-041). **The same rules as every other fetch**
        (pinned URL + size + SHA-256 + atomic install + rollback → setup.md §3b).

        **Nothing waits for this.** The index picks the Provider up on the next pass; a
        Lumi that had already started stays running, with search improving once it lands.
        """
        artifact = HARRIER_OSS_V1_270M
        await self._state.replace(
            embedding=EmbeddingSetup(
                state=EmbeddingSetupState.INSTALLING, model=artifact.name, progress=0.0
            )
        )

        async def progress(fraction: float) -> None:
            await self._state.replace(
                embedding=replace(self._state.snapshot.embedding, progress=fraction)
            )

        async def fail(reason: str) -> None:
            await self._state.replace(
                embedding=EmbeddingSetup(
                    state=EmbeddingSetupState.FAILED, model=artifact.name, reason=reason
                )
            )

        async def fetch() -> str:
            await install_model(artifact, paths.embedding_models_dir(), progress=throttle(progress))
            return artifact.name

        if await self._run(event="setup.embedding", run=fetch, fail=fail) is None:
            return

        await self._state.replace(
            embedding=EmbeddingSetup(state=EmbeddingSetupState.INSTALLED, model=artifact.name)
        )

    async def speech_model(self) -> None:
        """Fetches the speech-recognition model. **The same rules as the engine**
        (pinned URL + size + SHA-256 + atomic install + rollback → setup.md §3b).
        """
        artifact = selected_stt_artifact(self._env)
        if artifact is None:
            await self._state.replace(
                stt=SttSetup(state=SttSetupState.FAILED, model=None, reason="unpinned_model")
            )
            return
        await self._state.replace(
            stt=SttSetup(state=SttSetupState.INSTALLING, model=artifact.name, progress=0.0)
        )

        async def progress(fraction: float) -> None:
            await self._state.replace(stt=replace(self._state.snapshot.stt, progress=fraction))

        async def fail(reason: str) -> None:
            await self._state.replace(
                stt=SttSetup(state=SttSetupState.FAILED, model=artifact.name, reason=reason)
            )

        # **Returns the name rather than `None`.** `_install` reports failure as `None`, and
        # `install_model` returning nothing on success would make the two indistinguishable.
        async def fetch() -> str:
            await install_model(artifact, paths.stt_models_dir(), progress=throttle(progress))
            return artifact.name

        if await self._run(event="setup.model", run=fetch, fail=fail) is None:
            return

        await self._state.replace(stt=SttSetup(state=SttSetupState.INSTALLED, model=artifact.name))
