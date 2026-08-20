"""A dev-time verification probe. **Disabled by default.**

Only when `LUMI_DEV_OS_PROBE=1` does it hit `os.*` twice, right after Shell connects.

Verifies roadmap Phase 0 step 9 (**an unknown `os.*` command sent to Shell gets
rejected and logged**) against an actually-running Shell. Unit tests live in
`shell/src-tauri/src/os_command.rs`, but **whether the whole path is actually wired
up needs to be checked over the path itself.**

Not product behavior. Delete this once it's no longer needed in Phase 1 or beyond.
"""

from __future__ import annotations

from lumi import logging as lumi_logging
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)

ENV_FLAG = "LUMI_DEV_OS_PROBE"


async def probe_os_boundary(server: WsServer) -> None:
    """Sends one allowed command and one unknown command, and logs the results."""
    try:
        allowed = await server.invoke(
            Role.SHELL, "os.window.get_position", {"window": "stage"}, timeout=3.0
        )
        log.info("devprobe.allowed", ok=allowed.ok, payload=allowed.payload, error=allowed.error)

        # A command not on the allowlist. **Rejection is the correct outcome.**
        unknown = await server.invoke(Role.SHELL, "os.window.destroy", {}, timeout=3.0)
        log.info("devprobe.unknown", ok=unknown.ok, error=unknown.error)
        if unknown.ok:
            log.error("devprobe.b3_broken unknown os.* was not rejected")

        # Invariant 8's lane. Not implemented until Phase 4c, so rejection is the correct outcome.
        deferred = await server.invoke(
            Role.SHELL, "os.input.click", {"window": "permission", "x": 0, "y": 0}, timeout=3.0
        )
        log.info("devprobe.deferred", ok=deferred.ok, error=deferred.error)
        if deferred.ok:
            log.error("devprobe.b3_broken os.input.* was not rejected")
    # A probe failure never crashes Core (this isn't a product code path).
    except Exception as exc:
        log.warning("devprobe.failed", reason=str(exc))
