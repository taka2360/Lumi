"""The external TTS engine's process.

Design → docs/architecture/core.md §6 "Ownership and lifetime"

**Never touches something already running. Lumi only stops what Lumi itself started.**
Dragging down an engine the user started themselves, just because Lumi is exiting,
would overstep Lumi's authority.

No duplicate zombie-prevention here. The child process started here inherits the Job
Object Shell attached to Core, so it dies together with Shell if Shell is force-killed
(Windows).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from lumi import logging as lumi_logging
from lumi.providers.tts.aivisspeech import AivisSpeechClient
from lumi.setup.state import EngineRuntime

log = lumi_logging.get_logger(__name__)

#: Upper bound on waiting for startup. **The first run can take minutes because the
#: engine fetches its own voice model** (measured at ~2 minutes, docs/architecture/setup.md §4).
STARTUP_TIMEOUT_S = 300.0

#: Polling interval for checking startup.
POLL_INTERVAL_S = 1.0

#: Wait time for a single connectivity check. **Failing to connect mid-startup is
#: normal**, so this can be short.
PROBE_TIMEOUT_S = 1.0

#: Upper bound on waiting for shutdown. Force-killed once exceeded.
STOP_TIMEOUT_S = 10.0

#: Suppresses the console window on Windows.
#: **A black window popping up unprompted looks broken to the user.**
_CREATE_NO_WINDOW = 0x08000000


class EngineProcess:
    """Manages the lifetime of one external engine.

    The state vocabulary (`EngineRuntime`) lives in `lumi.setup.state`.
    It's the same single state distributed to the Stage, so **it's never defined in two places.**
    """

    def __init__(self, executable: Path | None, port: int) -> None:
        self._executable = executable
        self._port = port
        self._client = AivisSpeechClient(port)
        self._process: asyncio.subprocess.Process | None = None

    async def ensure_running(self) -> EngineRuntime:
        """Starts the engine if it isn't running, and waits for it to respond.

        **Does nothing if it's already running** (it might belong to someone else).
        """
        if await self._client.version(PROBE_TIMEOUT_S) is not None:
            log.info("tts.engine.already_running", port=self._port)
            return EngineRuntime.READY

        if self._executable is None:
            # Not running, and no way to start it either. **Never wait silently.**
            log.warning("tts.engine.no_executable", port=self._port)
            return EngineRuntime.FAILED

        try:
            self._process = await asyncio.create_subprocess_exec(
                str(self._executable),
                cwd=str(self._executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as error:
            log.warning("tts.engine.spawn_failed", error=str(error))
            return EngineRuntime.FAILED

        log.info("tts.engine.spawned", pid=self._process.pid, port=self._port)
        return await self._wait_until_ready()

    async def _wait_until_ready(self) -> EngineRuntime:
        """Waits until it responds. **Never waits forever.**

        If the process dies while waiting, this fails immediately instead of
        continuing to wait. Without distinguishing "slow to start" from "failed to
        start," the guidance shown to the user would be wrong.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STARTUP_TIMEOUT_S

        while loop.time() < deadline:
            if self._process is not None and self._process.returncode is not None:
                log.warning("tts.engine.exited_early", code=self._process.returncode)
                self._process = None
                return EngineRuntime.FAILED

            version = await self._client.version(PROBE_TIMEOUT_S)
            if version is not None:
                log.info("tts.engine.ready", version=version, port=self._port)
                return EngineRuntime.READY
            await asyncio.sleep(POLL_INTERVAL_S)

        log.warning("tts.engine.startup_timeout", seconds=STARTUP_TIMEOUT_S)
        return EngineRuntime.FAILED

    async def stop(self) -> None:
        """**Only stops what Lumi itself started.**

        The decision comes down to whether `self._process` is set. When
        `ensure_running` takes the "already running" path, it stays `None`, so **an
        engine the user started themselves never reaches this point.**
        """
        process = self._process
        if process is None:
            return
        self._process = None
        if process.returncode is not None:
            return

        process.terminate()
        try:
            async with asyncio.timeout(STOP_TIMEOUT_S):
                await process.wait()
        except TimeoutError:
            # Force it if it won't stop. **Never leave a process behind on exit.**
            log.warning("tts.engine.kill", pid=process.pid)
            process.kill()
            await process.wait()
        log.info("tts.engine.stopped", pid=process.pid)
