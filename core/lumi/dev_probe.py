"""開発時の検証用プローブ。**既定では動かない。**

`LUMI_DEV_OS_PROBE=1` のときだけ、Shell が接続した直後に `os.*` を2回叩く。

roadmap Phase 0 の検証手順 9（**未知の `os.*` コマンドを Shell に送ると拒否され、ログに残る**）
を、実際に走っている Shell に対して確認するためのもの。単体テストは
`shell/src-tauri/src/os_command.rs` にあるが、**経路全体が繋がっていることは経路で確かめる**。

製品の挙動ではない。Phase 1 以降でここが不要になったら消す。
"""

from __future__ import annotations

from lumi import logging as lumi_logging
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)

ENV_FLAG = "LUMI_DEV_OS_PROBE"


async def probe_os_boundary(server: WsServer) -> None:
    """許可されたコマンドと未知のコマンドを1回ずつ送り、結果をログに残す。"""
    try:
        allowed = await server.invoke(
            Role.SHELL, "os.window.get_position", {"window": "stage"}, timeout=3.0
        )
        log.info("devprobe.allowed", ok=allowed.ok, payload=allowed.payload, error=allowed.error)

        # allowlist に無いコマンド。**拒否されるのが正しい。**
        unknown = await server.invoke(Role.SHELL, "os.window.destroy", {}, timeout=3.0)
        log.info("devprobe.unknown", ok=unknown.ok, error=unknown.error)
        if unknown.ok:
            log.error("devprobe.b3_broken 未知の os.* が拒否されていない")

        # Invariant 8 の lane。Phase 4c まで実装しないので拒否されるのが正しい。
        deferred = await server.invoke(
            Role.SHELL, "os.input.click", {"window": "permission", "x": 0, "y": 0}, timeout=3.0
        )
        log.info("devprobe.deferred", ok=deferred.ok, error=deferred.error)
        if deferred.ok:
            log.error("devprobe.b3_broken os.input.* が拒否されていない")
    # プローブの失敗で Core を落とさない（これは製品の経路ではない）。
    except Exception as exc:
        log.warning("devprobe.failed", reason=str(exc))
