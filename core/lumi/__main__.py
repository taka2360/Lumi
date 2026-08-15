"""Core のエントリポイント。

起動シーケンス → docs/architecture/core.md §7
Phase 0 の実装対象は 1〜5（WS listen と token 認証）まで。
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading

from lumi import __version__
from lumi import logging as lumi_logging
from lumi.dev_probe import ENV_FLAG as DEV_PROBE_FLAG
from lumi.dev_probe import probe_os_boundary
from lumi.setup.coordinator import SetupCoordinator
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer, tokens_from_env

log = lumi_logging.get_logger(__name__)

#: Shell がこの環境変数を立てると、**標準入力が閉じた時点で終了する**。
#: 親（Shell）が強制終了されてもゾンビにならないための保険
#: （docs/interfaces/shell.md「Shell 終了時に Core も確実に終了する」）。
PARENT_WATCH_ENV = "LUMI_PARENT_WATCH"


def _watch_parent_via_stdin(stop: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    """stdin の EOF を待つ専用スレッド。EOF = 親が居なくなった。"""

    def run() -> None:
        try:
            while sys.stdin.readline():
                pass
        except (ValueError, OSError):
            pass
        log.info("core.parent_gone")
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=run, name="parent-watch", daemon=True).start()


async def _run() -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows の ProactorEventLoop は add_signal_handler を持たない。
            signal.signal(sig, lambda *_: stop.set())

    tokens, generated = tokens_from_env(os.environ)
    if generated:
        # 本番経路（Shell からのサイドカー起動）では必ず環境変数で渡ってくる。
        # 生成したということは、単体で起動されている = 開発時である。
        log.warning("core.token.generated", roles=[role.value for role in tokens])

    server: WsServer
    setup: SetupCoordinator

    async def on_connect(role: Role) -> None:
        # 接続ハンドラを塞がないように、時間のかかるものは別タスクで走らせる。
        if role is Role.SHELL and os.environ.get(DEV_PROBE_FLAG) == "1":
            asyncio.create_task(probe_os_boundary(server))  # noqa: RUF006
        if role is Role.STAGE:
            asyncio.create_task(setup.on_stage_connected())  # noqa: RUF006

    server = WsServer(tokens, port=int(os.environ.get("LUMI_WS_PORT", "0")), on_connect=on_connect)
    setup = SetupCoordinator(server, os.environ)
    port = await server.start()
    # **Shell はこの行を stdout から読んで接続先を知る。** イベント名を変えない。
    log.info("core.ws.listening", host="127.0.0.1", port=port)
    log.info("core.started", version=__version__, pid=os.getpid())

    # 検出はローカルのみ。**ここで外部通信は起きない**（docs/architecture/setup.md §1）。
    await setup.initialize()

    if os.environ.get(PARENT_WATCH_ENV) == "stdin":
        _watch_parent_via_stdin(stop, loop)

    try:
        await stop.wait()
    finally:
        log.info("core.stopping")
        await server.stop()
    return 0


def main() -> int:
    lumi_logging.configure(level=os.environ.get("LUMI_LOG_LEVEL", "INFO"))
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
