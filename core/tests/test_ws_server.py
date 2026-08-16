"""Contract tests for the WS server. **Needs neither an LLM nor an external engine.**"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from lumi.transport.protocol import PROTOCOL_VERSION, Role
from lumi.transport.server import (
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNAUTHORIZED,
    NotConnectedError,
    WsServer,
    tokens_from_env,
)

TOKENS = {Role.SHELL: "shell-token", Role.STAGE: "stage-token"}


@pytest.fixture
async def server() -> AsyncIterator[WsServer]:
    ws_server = WsServer(TOKENS)
    await ws_server.start()
    try:
        yield ws_server
    finally:
        await ws_server.stop()


async def open_client(
    server: WsServer, *, role: str = "shell", token: str | None = None
) -> ClientConnection:
    if token is None:
        token = TOKENS.get(Role(role), "") if role in {r.value for r in Role} else ""
    client = await connect(f"ws://127.0.0.1:{server.port}")
    await client.send(
        json.dumps({"v": PROTOCOL_VERSION, "kind": "hello", "role": role, "token": token})
    )
    return client


async def wait_until_connected(server: WsServer, role: Role) -> None:
    for _ in range(100):
        if server.is_connected(role):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{role} が接続されない")


class TestAuthentication:
    async def test_rejects_wrong_token(self, server: WsServer) -> None:
        client = await open_client(server, token="wrong")
        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_UNAUTHORIZED

    async def test_rejects_unknown_role(self, server: WsServer) -> None:
        client = await open_client(server, role="extension")
        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_PROTOCOL_ERROR

    async def test_accepts_valid_token(self, server: WsServer) -> None:
        client = await open_client(server)
        welcome = json.loads(await client.recv())
        assert welcome == {"v": PROTOCOL_VERSION, "kind": "welcome"}
        await client.close()

    async def test_rejects_silence(self, server: WsServer, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connection that never sends hello isn't held open forever."""
        monkeypatch.setattr("lumi.transport.server.HELLO_TIMEOUT_S", 0.05)
        client = await connect(f"ws://127.0.0.1:{server.port}")
        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_UNAUTHORIZED


class TestInvoke:
    async def test_roundtrip(self, server: WsServer) -> None:
        client = await open_client(server)
        await client.recv()  # welcome
        await wait_until_connected(server, Role.SHELL)

        async def respond() -> None:
            command = json.loads(await client.recv())
            assert command["method"] == "os.window.set_position"
            await client.send(
                json.dumps(
                    {"kind": "result", "corr_id": command["id"], "ok": True, "payload": {"x": 1}}
                )
            )

        responder = asyncio.create_task(respond())
        result = await server.invoke(Role.SHELL, "os.window.set_position", {"x": 1, "y": 2})
        await responder

        assert result.ok
        assert result.payload == {"x": 1}
        await client.close()

    async def test_refuses_os_namespace_to_stage(self, server: WsServer) -> None:
        """**`stage.*` must never request OS privileges** (B2). Rejected before sending."""
        client = await open_client(server, role="stage")
        await client.recv()
        await wait_until_connected(server, Role.STAGE)

        with pytest.raises(ValueError, match="namespace"):
            await server.invoke(Role.STAGE, "os.input.click", {})
        await client.close()

    async def test_refuses_stage_namespace_to_shell(self, server: WsServer) -> None:
        client = await open_client(server)
        await client.recv()
        await wait_until_connected(server, Role.SHELL)

        with pytest.raises(ValueError, match="namespace"):
            await server.invoke(Role.SHELL, "stage.character.speak", {})
        await client.close()

    async def test_fails_loudly_when_not_connected(self, server: WsServer) -> None:
        """**Never silently degrades.** Reports that the peer is absent via an exception."""
        with pytest.raises(NotConnectedError):
            await server.invoke(Role.SHELL, "os.window.set_position", {})


class TestClientMessages:
    async def test_client_cannot_send_commands(self, server: WsServer) -> None:
        """A `command` from the client to Core is never accepted. **The whole connection is closed.**"""
        client = await open_client(server)
        await client.recv()
        await client.send(
            json.dumps({"kind": "command", "id": "1", "method": "os.input.click", "payload": {}})
        )
        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_PROTOCOL_ERROR


class TestReconnect:
    async def test_new_connection_replaces_the_old_one(self, server: WsServer) -> None:
        first = await open_client(server)
        await first.recv()
        await wait_until_connected(server, Role.SHELL)

        second = await open_client(server)
        await second.recv()
        with pytest.raises(ConnectionClosed):
            await first.recv()

        assert server.is_connected(Role.SHELL)
        await second.close()


class TestToken:
    def test_uses_env_tokens(self) -> None:
        tokens, generated = tokens_from_env(
            {"LUMI_WS_TOKEN_SHELL": "a", "LUMI_WS_TOKEN_STAGE": "b"}
        )
        assert tokens == {Role.SHELL: "a", Role.STAGE: "b"}
        assert not generated

    def test_generates_when_missing(self) -> None:
        tokens, generated = tokens_from_env({})
        assert generated
        assert all(len(value) >= 32 for value in tokens.values())

    def test_refuses_empty_token(self) -> None:
        with pytest.raises(ValueError, match="token"):
            WsServer({Role.SHELL: "a", Role.STAGE: ""})


class TestRoleTokenIsolation:
    """A compromised Stage cannot authenticate as shell (B2 / B3)."""

    async def test_stage_token_cannot_authenticate_as_shell(self, server: WsServer) -> None:
        client = await open_client(server, role="shell", token=TOKENS[Role.STAGE])
        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_UNAUTHORIZED

    async def test_shell_token_cannot_authenticate_as_stage(self, server: WsServer) -> None:
        client = await open_client(server, role="stage", token=TOKENS[Role.SHELL])
        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_UNAUTHORIZED


class TestNotify:
    async def test_delivers_without_a_result(self, server: WsServer) -> None:
        client = await open_client(server, role="stage")
        await client.recv()  # welcome
        await wait_until_connected(server, Role.STAGE)

        await server.notify(Role.STAGE, "stage.setup.state", {"state": "not_configured"})
        message = json.loads(await client.recv())
        assert message["kind"] == "notify"
        assert message["method"] == "stage.setup.state"
        assert message["payload"] == {"state": "not_configured"}
        await client.close()

    async def test_dropped_silently_when_nobody_listens(self, server: WsServer) -> None:
        """A notification not arriving isn't a failure. **Never blocks Core's own progress.**"""
        await server.notify(Role.STAGE, "stage.setup.state", {})

    async def test_still_refuses_namespace_violations(self, server: WsServer) -> None:
        with pytest.raises(ValueError, match="namespace"):
            await server.notify(Role.STAGE, "os.input.click", {})
