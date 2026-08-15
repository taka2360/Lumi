"""WS サーバ。**Core がハブ**なので、Core が listen し、Shell と Stage が接続してくる。

起動シーケンス → docs/architecture/core.md §7
境界 → docs/contracts/security-boundaries.md B2（Core ↔ Stage）/ B3（Core → Shell）

## 認証

`token` は Shell がサイドカー起動時に生成し、**環境変数**で Core に渡す
（コマンドラインに載せない → docs/interfaces/shell.md）。Core は 127.0.0.1 にだけ bind する。

## この層が保証しないこと

token を知っているプロセスは接続できる。**同一マシン上の他プロセスからの接続を防げるのは、
token が漏れていない限りにおいてである。** それ以上の隔離（プロセス署名の検証など）はしていない。
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from lumi import logging as lumi_logging
from lumi.transport.protocol import (
    HELLO_TIMEOUT_S,
    PING_INTERVAL_S,
    PING_TIMEOUT_S,
    Command,
    ProtocolError,
    Result,
    Role,
    Welcome,
    method_matches_role,
    new_id,
    parse_client_message,
    parse_hello,
)

log = lumi_logging.get_logger(__name__)

#: 認証失敗時に返す close code（1000-2999 は予約なので 4000 番台を使う）。
CLOSE_UNAUTHORIZED = 4401
CLOSE_PROTOCOL_ERROR = 4400

#: command の既定タイムアウト。応答が来ないことを**黙って待ち続けない**。
DEFAULT_COMMAND_TIMEOUT_S = 10.0


class NotConnectedError(RuntimeError):
    """相手が繋がっていない。**黙って劣化させないために例外にする。**"""


class CommandFailedError(RuntimeError):
    """相手が拒否した、または実行に失敗した。"""


class _Connection:
    """認証済みの1接続。role ごとに高々1つ。"""

    def __init__(self, role: Role, ws: ServerConnection) -> None:
        self.role = role
        self.ws = ws
        self._pending: dict[str, asyncio.Future[Result]] = {}

    def register(self, command_id: str) -> asyncio.Future[Result]:
        future: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        return future

    def resolve(self, result: Result) -> bool:
        future = self._pending.pop(result.corr_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(result)
        return True

    def abandon_all(self, reason: str) -> None:
        """接続が切れた。待っている command を**エラーで終わらせる**（宙ぶらりんにしない）。"""
        for command_id, future in self._pending.items():
            if not future.done():
                future.set_exception(NotConnectedError(f"{command_id}: {reason}"))
        self._pending.clear()


ConnectHook = Callable[[Role], Awaitable[None]]


class WsServer:
    """Core の WS サーバ。"""

    def __init__(
        self,
        token: str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        on_connect: ConnectHook | None = None,
        on_disconnect: ConnectHook | None = None,
    ) -> None:
        if not token:
            raise ValueError("token が空。認証なしで listen しない（fail-closed）")
        self._token = token
        self._host = host
        self._requested_port = port
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._connections: dict[Role, _Connection] = {}
        self._server: Server | None = None
        self._port: int | None = None

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("まだ listen していない")
        return self._port

    def is_connected(self, role: Role) -> bool:
        return role in self._connections

    async def start(self) -> int:
        """listen を開始し、実際のポート番号を返す。"""
        self._server = await serve(
            self._handle,
            self._host,
            self._requested_port,
            ping_interval=PING_INTERVAL_S,
            ping_timeout=PING_TIMEOUT_S,
        )
        sockets = list(self._server.sockets)
        if not sockets:
            raise RuntimeError("listen に失敗した")
        self._port = int(sockets[0].getsockname()[1])
        return self._port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ASYNC109: 呼び出し側に `asyncio.timeout` を任せると、書き忘れたときに**永遠に待つ**。
    # 「黙って劣化しない」ために、既定値つきの timeout を API に持たせる。
    async def invoke(
        self,
        role: Role,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_COMMAND_TIMEOUT_S,  # noqa: ASYNC109
    ) -> Result:
        """command を送って result を待つ。

        **namespace と role の不一致は送信前に落とす。**
        Stage に `os.*` を送れる経路を作らないため（B2）。
        """
        if not method_matches_role(method, role):
            raise ValueError(f"{role.value} に {method} は送れない（namespace 違反）")

        connection = self._connections.get(role)
        if connection is None:
            raise NotConnectedError(f"{role.value} が接続していない")

        command = Command(id=new_id(), method=method, payload=payload or {})
        future = connection.register(command.id)
        try:
            await connection.ws.send(command.to_json())
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            log.warning("transport.command.timeout", role=role.value, method=method)
            raise
        finally:
            connection.resolve(Result(corr_id=command.id, ok=False, payload={}, error="cleanup"))

    async def _handle(self, ws: ServerConnection) -> None:
        role = await self._authenticate(ws)
        if role is None:
            return

        connection = _Connection(role, ws)
        previous = self._connections.get(role)
        if previous is not None:
            # 再接続。古い方を捨てる（Core 側に幽霊接続を残さない）。
            log.info("transport.reconnect", role=role.value)
            previous.abandon_all("再接続で置き換えられた")
            await previous.ws.close(code=1000, reason="replaced by a new connection")

        self._connections[role] = connection
        log.info("transport.connected", role=role.value)
        if self._on_connect is not None:
            await self._on_connect(role)

        try:
            await self._receive_loop(connection)
        finally:
            if self._connections.get(role) is connection:
                del self._connections[role]
                connection.abandon_all("接続が閉じた")
                log.info("transport.disconnected", role=role.value)
                if self._on_disconnect is not None:
                    await self._on_disconnect(role)

    async def _authenticate(self, ws: ServerConnection) -> Role | None:
        """hello を待って token を検証する。**失敗したら閉じる。**"""
        try:
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT_S)
        except TimeoutError:
            log.warning("transport.hello.timeout")
            await ws.close(code=CLOSE_UNAUTHORIZED, reason="hello timeout")
            return None
        except ConnectionClosed:
            return None

        if isinstance(raw, bytes):
            log.warning("transport.hello.binary")
            await ws.close(code=CLOSE_PROTOCOL_ERROR, reason="hello must be text")
            return None

        try:
            hello = parse_hello(raw)
        except ProtocolError as exc:
            log.warning("transport.hello.invalid", reason=str(exc))
            await ws.close(code=CLOSE_PROTOCOL_ERROR, reason="invalid hello")
            return None

        # 比較はタイミング攻撃を避ける形で行う。
        if not secrets.compare_digest(hello.token, self._token):
            log.warning("transport.auth.rejected", role=hello.role.value)
            await ws.close(code=CLOSE_UNAUTHORIZED, reason="invalid token")
            return None

        await ws.send(Welcome().to_json())
        return hello.role

    async def _receive_loop(self, connection: _Connection) -> None:
        async for raw in connection.ws:
            if isinstance(raw, bytes):
                log.warning("transport.message.binary", role=connection.role.value)
                await connection.ws.close(code=CLOSE_PROTOCOL_ERROR, reason="text only")
                return
            try:
                result = parse_client_message(raw)
            except ProtocolError as exc:
                # クライアントが契約を破った。**握りつぶさずに閉じる。**
                log.warning(
                    "transport.message.invalid", role=connection.role.value, reason=str(exc)
                )
                await connection.ws.close(code=CLOSE_PROTOCOL_ERROR, reason="protocol error")
                return

            if not connection.resolve(result):
                log.warning(
                    "transport.result.unknown_corr_id",
                    role=connection.role.value,
                    corr_id=result.corr_id,
                )


def token_from_env(env: dict[str, str]) -> tuple[str, bool]:
    """環境変数から token を取り出す。無ければ生成する。

    戻り値の 2 番目は「生成したか」。**生成した場合は開発時のみ**であり、
    Shell から起動された本番経路では必ず環境変数で渡ってくる。
    """
    token = env.get("LUMI_WS_TOKEN", "")
    if token:
        return token, False
    return secrets.token_urlsafe(32), True
