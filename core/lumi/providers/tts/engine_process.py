"""外部 TTS エンジンのプロセス。

設計 → docs/architecture/core.md §6「所有と生存」

**すでに動いているものには触らない。Lumi が起動したものだけ Lumi が止める。**
ユーザーが自分で起動したエンジンを、Lumi の終了に巻き込んで落とすのは越権である。

ゾンビ対策を二重に持たない。ここで起動した子プロセスは、Shell が Core に付けた
Job Object を継承するので、Shell が強制終了されても一緒に落ちる（Windows）。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from lumi import logging as lumi_logging
from lumi.providers.tts.aivisspeech import AivisSpeechClient
from lumi.setup.state import EngineRuntime

log = lumi_logging.get_logger(__name__)

#: 起動を待つ上限。**初回はエンジン自身が音声モデルを取りに行くので数分かかる**
#: （docs/architecture/setup.md §4 の実測: 約2分）。
STARTUP_TIMEOUT_S = 300.0

#: 起動確認のポーリング間隔。
POLL_INTERVAL_S = 1.0

#: 1回の疎通確認の待ち時間。**起動途中は繋がらないのが正常**なので短くてよい。
PROBE_TIMEOUT_S = 1.0

#: 終了を待つ上限。過ぎたら強制終了する。
STOP_TIMEOUT_S = 10.0

#: Windows でコンソールウィンドウを出さない。
#: **ユーザーから見て「勝手に黒い窓が出る」のは壊れて見える。**
_CREATE_NO_WINDOW = 0x08000000


class EngineProcess:
    """1つの外部エンジンの生存管理。

    状態の語彙（`EngineRuntime`）は `lumi.setup.state` が持つ。
    Stage に配るのは同じ1つの状態なので、**定義を2箇所に置かない**。
    """

    def __init__(self, executable: Path | None, port: int) -> None:
        self._executable = executable
        self._port = port
        self._client = AivisSpeechClient(port)
        self._process: asyncio.subprocess.Process | None = None

    @property
    def owned(self) -> bool:
        """Lumi が起動したものか。**止めてよいかの判定はこれだけで決まる。**"""
        return self._process is not None

    async def ensure_running(self) -> EngineRuntime:
        """起動していなければ起動し、応答するまで待つ。

        **すでに動いていれば何もしない**（他人のプロセスかもしれない）。
        """
        if await self._client.version(PROBE_TIMEOUT_S) is not None:
            log.info("tts.engine.already_running", port=self._port)
            return EngineRuntime.READY

        if self._executable is None:
            # 動いてもいないし、起動する手段も無い。**黙って待たない。**
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
        """応答するまで待つ。**無限に待たない。**

        待っている間にプロセスが死んだら、待ち続けずに失敗にする。
        「起動が遅い」と「起動に失敗した」を区別できないと、ユーザーへの案内が嘘になる。
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
        """**Lumi が起動したものだけ止める。**"""
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
            # 止まらないなら強制する。**終了時にプロセスを残さない。**
            log.warning("tts.engine.kill", pid=process.pid)
            process.kill()
            await process.wait()
        log.info("tts.engine.stopped", pid=process.pid)
