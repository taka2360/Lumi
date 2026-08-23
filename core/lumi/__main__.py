"""Core's entry point.

Startup sequence → docs/architecture/core.md §7
Phase 0 implements steps 1-5 (WS listen and token auth).
"""

from __future__ import annotations

import asyncio
import io
import os
import signal
import sys
import threading

from lumi import __version__
from lumi import logging as lumi_logging
from lumi.agent.runtime import ConversationRuntime
from lumi.audio.devices import AudioPlan, plan_audio
from lumi.audio.probe import list_devices
from lumi.dev_probe import ENV_FLAG as DEV_PROBE_FLAG
from lumi.dev_probe import probe_os_boundary
from lumi.setup.coordinator import SetupCoordinator
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer, tokens_from_env

log = lumi_logging.get_logger(__name__)

#: When Shell sets this environment variable, **Core exits the moment stdin closes**.
#: A safety net against becoming a zombie process if the parent (Shell) is force-killed
#: (docs/interfaces/shell.md "Core reliably exits when Shell exits").
PARENT_WATCH_ENV = "LUMI_PARENT_WATCH"


def _watch_parent_via_stdin(stop: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    """Dedicated thread that waits for stdin EOF. EOF = the parent is gone."""

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
            # Windows's ProactorEventLoop doesn't have add_signal_handler.
            signal.signal(sig, lambda *_: stop.set())

    tokens, generated = tokens_from_env(os.environ)
    if generated:
        # In the production path (launched as a sidecar from Shell), these always arrive
        # via environment variables. If we generated them, we're running standalone = dev time.
        log.warning("core.token.generated", roles=[role.value for role in tokens])

    server: WsServer
    setup: SetupCoordinator
    conversation: ConversationRuntime | None = None

    async def on_stage_connected() -> None:
        # **Only start the conversation once setup is done.** Listening while setup is
        # still fetching things would mix progress display with speech and make it unclear
        # what's happening.
        nonlocal conversation
        await setup.on_stage_connected()
        if conversation is not None:
            return
        conversation = ConversationRuntime(server, setup, _audio_plan())
        await conversation.start()

    async def on_connect(role: Role) -> None:
        # Run anything slow in a separate task so it doesn't block the connect handler.
        if role is Role.SHELL and os.environ.get(DEV_PROBE_FLAG) == "1":
            asyncio.create_task(probe_os_boundary(server))  # noqa: RUF006
        if role is Role.STAGE:
            asyncio.create_task(on_stage_connected())  # noqa: RUF006
        # **A panel window opened.** It has missed every broadcast so far, so it is handed
        # the current state rather than being left to wait for the next change (ADR-042).
        if role is Role.PANEL and conversation is not None:
            asyncio.create_task(conversation.on_panel_connected())  # noqa: RUF006

    server = WsServer(tokens, port=int(os.environ.get("LUMI_WS_PORT", "0")), on_connect=on_connect)
    setup = SetupCoordinator(server, os.environ)
    port = await server.start()
    # **Shell reads this line from stdout to learn where to connect.** Don't rename this event.
    log.info("core.ws.listening", host="127.0.0.1", port=port)
    log.info("core.started", version=__version__, pid=os.getpid())

    # Detection is local only. **No external communication happens here**
    # (docs/architecture/setup.md §1).
    await setup.initialize()

    if os.environ.get(PARENT_WATCH_ENV) == "stdin":
        _watch_parent_via_stdin(stop, loop)

    try:
        await stop.wait()
    finally:
        log.info("core.stopping")
        # **Only stop engines that Lumi itself started** (docs/architecture/core.md §6).
        if conversation is not None:
            await conversation.stop()
        await server.stop()
    return 0


def _audio_plan() -> AudioPlan:
    """Look at devices and decide which streams to open.

    **Having zero input devices is a normal state** (docs/architecture/audio.md).
    Startup continues even if enumeration itself fails — Lumi without sound is still Lumi.
    """
    try:
        devices, host_apis = list_devices()
    except Exception as error:
        log.warning("audio.probe_failed", error=str(error))
        return AudioPlan(capture=None, playback=None, warnings=("Could not list audio devices",))
    return plan_audio(devices, host_apis)


def _force_utf8_stdio() -> None:
    """**Force stdout to UTF-8.**

    Running the packed executable (PyInstaller) on Japanese Windows defaults stdout to
    cp932. Core's logs contain Japanese, and **Shell is contractually reading them from
    stdout**, so the moment a character outside cp932 shows up, Core crashes with
    `UnicodeEncodeError` (observed 2026-08-15).

    This goes unnoticed in dev (`uv run`), which uses UTF-8. **A bug that only breaks in
    the packaged build.** `errors="replace"` is used because **Core dying is worse than
    one unreadable log line.**
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _force_utf8_stdio()
    if "--self-check" in sys.argv[1:]:
        # **The path that verifies the distributable works on this PC**
        # (docs/architecture/setup.md). Doesn't start WS or anything else. Imported here so normal
        # startup isn't made heavier.
        from lumi.selfcheck import run

        return run()

    lumi_logging.configure(level=os.environ.get("LUMI_LOG_LEVEL", "INFO"))
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
