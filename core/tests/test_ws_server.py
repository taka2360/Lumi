"""Contract tests for the WS server. **Needs neither an LLM nor an external engine.**"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from lumi.transport import server as server_module
from lumi.transport.protocol import PROTOCOL_VERSION, Role
from lumi.transport.server import (
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNAUTHORIZED,
    NotConnectedError,
    RequestRefused,
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
    raise AssertionError(f"{role} did not connect")


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
            assert command["v"] == PROTOCOL_VERSION
            assert command["method"] == "os.window.set_position"
            await client.send(
                json.dumps(
                    {
                        "v": PROTOCOL_VERSION,
                        "kind": "result",
                        "corr_id": command["id"],
                        "ok": True,
                        "payload": {"x": 1},
                    }
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
        """A `command` from the client to Core is never accepted. **The whole connection is
        closed.**
        """
        client = await open_client(server)
        await client.recv()
        await client.send(
            json.dumps({"kind": "command", "id": "1", "method": "os.input.click", "payload": {}})
        )
        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_PROTOCOL_ERROR

    async def test_a_version_mismatch_is_logged_and_closes_the_connection(
        self, server: WsServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**A frame with an unknown meaning is not dropped and ignored.**"""
        client = await open_client(server)
        await client.recv()
        await client.send(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION + 1,
                    "kind": "request",
                    "id": "bad-version",
                    "method": "stage.settings.update",
                    "payload": {},
                }
            )
        )

        with pytest.raises(ConnectionClosed):
            await client.recv()
        assert client.close_code == CLOSE_PROTOCOL_ERROR

        captured = ""
        for _ in range(100):
            captured += capsys.readouterr().out
            if "transport.message.invalid" in captured:
                break
            await asyncio.sleep(0.01)
        assert "transport.message.invalid" in captured


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
        assert message["v"] == PROTOCOL_VERSION
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


class TestInboundRequests:
    """Stage → Core. **ADR-028.**

    Every test here is about the same thing: **the Stage may ask, and Core decides.**
    """

    async def send_request(
        self, client: ClientConnection, method: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        await client.send(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "request",
                    "id": "r1",
                    "method": method,
                    "payload": payload or {},
                }
            )
        )
        answer: dict[str, object] = json.loads(await client.recv())
        assert answer["v"] == PROTOCOL_VERSION
        return answer

    async def test_a_registered_method_is_served(self, server: WsServer) -> None:
        async def handler(payload: dict[str, object]) -> dict[str, object]:
            return {"echo": payload.get("x")}

        server.on_request("stage.settings.update", handler)
        client = await open_client(server, role="stage")
        await client.recv()

        answer = await self.send_request(client, "stage.settings.update", {"x": 42})

        assert answer["ok"] is True
        assert answer["payload"] == {"echo": 42}
        assert answer["corr_id"] == "r1"

    async def test_an_unregistered_method_is_refused(self, server: WsServer) -> None:
        """★ **The allowlist is the registry.** A route nobody wrote down does not exist."""
        client = await open_client(server, role="stage")
        await client.recv()

        answer = await self.send_request(client, "stage.anything.at.all")

        assert answer["ok"] is False
        assert answer["error"] == "unknown_method"

    async def test_the_connection_survives_a_refusal(self, server: WsServer) -> None:
        """**Refusing is not the same as being attacked.** A typo must not drop the Stage,
        or one bad request would take the character off screen.
        """
        client = await open_client(server, role="stage")
        await client.recv()

        await self.send_request(client, "stage.nope")
        server.on_request("stage.settings.update", lambda _payload: _ok())
        answer = await self.send_request(client, "stage.settings.update")

        assert answer["ok"] is True

    async def test_the_other_namespace_is_refused_even_if_registered(
        self, server: WsServer
    ) -> None:
        """★ **`stage.*` must never request OS privileges** (docs/architecture/core.md §3).

        Registration alone is not enough: the namespace has to match the role that asked.
        """
        server.on_request("os.window.set_position", lambda _payload: _ok())
        client = await open_client(server, role="stage")
        await client.recv()

        answer = await self.send_request(client, "os.window.set_position")

        assert answer["ok"] is False
        assert answer["error"] == "unknown_method"

    async def test_a_refusing_handler_sends_its_reason_back(self, server: WsServer) -> None:
        async def handler(_payload: dict[str, object]) -> dict[str, object]:
            raise RequestRefused("SettingsUnreadable")

        server.on_request("stage.settings.update", handler)
        client = await open_client(server, role="stage")
        await client.recv()

        answer = await self.send_request(client, "stage.settings.update")

        assert answer["ok"] is False
        assert answer["error"] == "SettingsUnreadable"

    async def test_a_crashing_handler_never_leaks_its_message(self, server: WsServer) -> None:
        """**The client is not told what broke.** The full exception is logged instead."""

        async def handler(_payload: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("Cannot open C:/Users/secret/path")

        server.on_request("stage.settings.update", handler)
        client = await open_client(server, role="stage")
        await client.recv()

        answer = await self.send_request(client, "stage.settings.update")

        assert answer["ok"] is False
        assert answer["error"] == "internal_error"

    async def test_a_slow_handler_does_not_block_the_next_frame(self, server: WsServer) -> None:
        """**Each request is served in its own task.** Otherwise a slow handler would queue
        behind it the `result` that Core itself is waiting on.
        """
        started = asyncio.Event()

        async def slow(_payload: dict[str, object]) -> dict[str, object]:
            started.set()
            await asyncio.sleep(5.0)
            return {}

        server.on_request("stage.settings.update", slow)
        client = await open_client(server, role="stage")
        await client.recv()

        await client.send(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "request",
                    "id": "slow",
                    "method": "stage.settings.update",
                    "payload": {},
                }
            )
        )
        async with asyncio.timeout(2.0):
            await started.wait()
            # The connection is still reading: a second request gets its own answer
            answer = await self.send_request(client, "stage.other")
        assert answer["error"] == "unknown_method"

    async def test_a_handler_that_never_returns_is_still_answered(
        self, server: WsServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ Regression: **a hung handler used to leave the client waiting forever.**

        `_serve_request` promises to always answer. Without a bound, a handler that blocks
        makes Core indistinguishable from a dead one — from the Stage's side there is simply
        no reply, ever.
        """
        monkeypatch.setattr(server_module, "INBOUND_REQUEST_TIMEOUT_S", 0.05)

        async def hangs(_payload: dict[str, object]) -> dict[str, object]:
            await asyncio.sleep(30.0)
            return {}

        server.on_request("stage.settings.update", hangs)
        client = await open_client(server, role="stage")
        await client.recv()

        async with asyncio.timeout(2.0):
            answer = await self.send_request(client, "stage.settings.update")

        assert answer["ok"] is False
        assert answer["error"] == "timeout"

    async def test_an_unencodable_payload_gets_an_internal_error_fallback(
        self, server: WsServer
    ) -> None:
        """**The client must not wait forever when Core cannot encode its answer.**"""

        async def returns_garbage(_payload: dict[str, object]) -> dict[str, object]:
            return {"not_json": object()}

        server.on_request("stage.settings.update", returns_garbage)
        client = await open_client(server, role="stage")
        await client.recv()

        await client.send(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "request",
                    "id": "bad",
                    "method": "stage.settings.update",
                    "payload": {},
                }
            )
        )
        answer = json.loads(await client.recv())
        assert answer["ok"] is False
        assert answer["payload"] == {}
        assert answer["error"] == "internal_error"

    async def test_stop_cancels_and_awaits_inbound_handlers(
        self, server: WsServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hangs(_payload: dict[str, object]) -> dict[str, object]:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return {}

        server.on_request("stage.settings.update", hangs)
        client = await open_client(server, role="stage")
        await client.recv()
        await client.send(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "kind": "request",
                    "id": "shutdown",
                    "method": "stage.settings.update",
                    "payload": {},
                }
            )
        )
        async with asyncio.timeout(2.0):
            await started.wait()

        ws_server = server._server
        assert ws_server is not None
        wait_closed = ws_server.wait_closed

        async def wait_closed_after_handlers() -> None:
            await cancelled.wait()
            await wait_closed()

        monkeypatch.setattr(ws_server, "wait_closed", wait_closed_after_handlers)
        async with asyncio.timeout(2.0):
            await server.stop()

        assert cancelled.is_set()
        assert not server._requests

    def test_registering_the_same_method_twice_is_refused(self, server: WsServer) -> None:
        """**Two owners for one route** is how a request quietly stops reaching the handler
        someone expected.
        """
        server.on_request("stage.settings.update", lambda _payload: _ok())
        with pytest.raises(ValueError, match=r"stage\.settings\.update"):
            server.on_request("stage.settings.update", lambda _payload: _ok())


async def _ok() -> dict[str, object]:
    return {}
