"""Core's entry point wiring. **Neither an engine nor a device is involved** (both substituted).

`ConversationRuntime` itself is covered by `test_agent_runtime.py`. What is only visible
here is **how many of them the Stage's connections end up creating.**
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from pathlib import Path
from typing import Any, ClassVar

import pytest
from websockets.asyncio.client import ClientConnection, connect

from lumi import __main__ as main_module
from lumi.audio.devices import AudioPlan
from lumi.setup import coordinator as coordinator_module
from lumi.transport.protocol import PROTOCOL_VERSION

STAGE_TOKEN = "stage-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


class SlowRuntime:
    """**Awaits inside `start()`** — that is the window the race needed."""

    created: ClassVar[list[SlowRuntime]] = []

    def __init__(self, *_args: Any) -> None:
        SlowRuntime.created.append(self)
        self.starts = 0

    async def start(self) -> None:
        await asyncio.sleep(0.05)
        self.starts += 1

    async def stop(self) -> None:
        pass


async def open_stage(port: int) -> ClientConnection:
    client = await connect(f"ws://127.0.0.1:{port}")
    await client.send(
        json.dumps({"v": PROTOCOL_VERSION, "kind": "hello", "role": "stage", "token": STAGE_TOKEN})
    )
    await client.recv()
    return client


async def test_two_stage_connects_start_exactly_one_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**One conversation, however many times the Stage connects.**

    Both connects queue behind detection inside `setup.on_stage_connected()` and come out
    together. What keeps that from producing two runtimes is that the `is not None` check
    and the assignment sit next to each other with no `await` between them — **put one
    there and each connect gets its own microphone**, with `stop()` reaching only the last.
    """
    SlowRuntime.created = []
    port = free_port()

    async def detect(_env: Any) -> list[Any]:
        # **Detection is what the connects wait behind.** Slow enough for both to queue up
        await asyncio.sleep(0.05)
        return []

    async def detect_ollama(_env: Any) -> None:
        # Keep this wiring test independent of a real local Ollama/API timeout. The race
        # under test is the Stage connection, not the provider's one-second probe.
        return None

    monkeypatch.setattr(coordinator_module, "detect_engines", detect)
    monkeypatch.setattr(coordinator_module, "detect_ollama", detect_ollama)
    monkeypatch.setattr(main_module, "ConversationRuntime", SlowRuntime)
    monkeypatch.setattr(
        main_module, "_audio_plan", lambda: AudioPlan(capture=None, playback=None, warnings=())
    )
    monkeypatch.setenv("LUMI_WS_TOKEN_SHELL", "shell-token")
    monkeypatch.setenv("LUMI_WS_TOKEN_STAGE", STAGE_TOKEN)
    monkeypatch.setenv("LUMI_WS_PORT", str(port))

    core = asyncio.create_task(main_module._run())
    try:
        first, second = await asyncio.gather(open_stage(port), open_stage(port))
        await asyncio.sleep(0.3)
        await first.close()
        await second.close()

        assert len(SlowRuntime.created) == 1, "multiple conversation runtimes created on connect"
        assert SlowRuntime.created[0].starts == 1
    finally:
        core.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await core


class TrackedRuntime:
    """Records whether it was ever left running."""

    created: ClassVar[list[TrackedRuntime]] = []

    def __init__(self, *_args: Any) -> None:
        TrackedRuntime.created.append(self)
        self.running = False

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def on_panel_connected(self) -> None:
        pass


async def test_shutdown_leaves_no_conversation_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Nothing may come up after teardown decided there was nothing to bring down.**

    `on_stage_connected` waits on detection, then assigns `conversation` and starts it.
    Teardown reads that same variable. Without cancelling the handler first, a connect
    still parked in detection resumes during `server.stop()` — after the check found
    `None` — and opens the microphone and the engines of a Lumi that is already gone.

    The window is real on a quit during first-run setup, where detection is the slow part.
    """
    TrackedRuntime.created = []
    port = free_port()

    detecting = asyncio.Event()

    async def detect(_env: Any) -> list[Any]:
        # **Says when the handler is actually parked here**, rather than letting the test
        # guess with a sleep. The window under test is "still inside detection", so a
        # guess that lands early tests nothing and a guess that lands late tests nothing
        # else.
        detecting.set()
        await asyncio.sleep(0.2)
        return []

    async def detect_ollama(_env: Any) -> None:
        return None

    monkeypatch.setattr(coordinator_module, "detect_engines", detect)
    monkeypatch.setattr(coordinator_module, "detect_ollama", detect_ollama)
    monkeypatch.setattr(main_module, "ConversationRuntime", TrackedRuntime)
    monkeypatch.setattr(
        main_module, "_audio_plan", lambda: AudioPlan(capture=None, playback=None, warnings=())
    )
    monkeypatch.setenv("LUMI_WS_TOKEN_SHELL", "shell-token")
    monkeypatch.setenv("LUMI_WS_TOKEN_STAGE", STAGE_TOKEN)
    monkeypatch.setenv("LUMI_WS_PORT", str(port))

    core = asyncio.create_task(main_module._run())
    client = await open_stage(port)

    # Quit while the connect handler is still inside detection.
    await asyncio.wait_for(detecting.wait(), timeout=5)
    core.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await core
    await client.close()

    # Long enough for a handler that outlived the stop to finish and start something.
    await asyncio.sleep(0.3)
    assert [runtime for runtime in TrackedRuntime.created if runtime.running] == []
