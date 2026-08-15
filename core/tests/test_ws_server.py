"""WS サーバの契約テスト。**LLM も外部エンジンも要らない。**"""

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
    token_from_env,
)

TOKEN = "test-token"


@pytest.fixture
async def server() -> AsyncIterator[WsServer]:
    ws_server = WsServer(TOKEN)
    await ws_server.start()
    try:
        yield ws_server
    finally:
        await ws_server.stop()


async def open_client(
    server: WsServer, *, role: str = "shell", token: str = TOKEN
) -> ClientConnection:
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
        """hello を送らない接続を掴み続けない。"""
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
        """**`stage.*` は絶対に OS 特権を要求しない**（B2）。送信前に落とす。"""
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
        """**黙って劣化しない。** 相手が居ないことを例外で伝える。"""
        with pytest.raises(NotConnectedError):
            await server.invoke(Role.SHELL, "os.window.set_position", {})


class TestClientMessages:
    async def test_client_cannot_send_commands(self, server: WsServer) -> None:
        """クライアントから Core への command は受理しない。**接続ごと閉じる。**"""
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
    def test_uses_env_token(self) -> None:
        assert token_from_env({"LUMI_WS_TOKEN": "from-env"}) == ("from-env", False)

    def test_generates_when_missing(self) -> None:
        token, generated = token_from_env({})
        assert generated
        assert len(token) >= 32

    def test_refuses_empty_token(self) -> None:
        with pytest.raises(ValueError, match="token"):
            WsServer("")
