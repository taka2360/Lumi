"""Grant — a scoped token. **Not a boolean.**

Design → docs/architecture/permission.md §5

What this can express:

- "For this session, reading is allowed under `C:\\Users\\yuasa\\Projects`"
- "This URL may be accessed exactly once"
- "Writing to this folder is allowed for the rest of today"

> **Never put "allow everything" in the UI.** The instant it's an option, the user
> will always pick it.

**No Grant is ever created in Phase 1** (there are only L0 tools, so `ask` is never
reached). Only the path and the types are kept identical to production. The UI and
issuance land in Phase 4a.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NewType

from lumi.permission.scope import SecurityScope

GrantId = NewType("GrantId", str)


@dataclass(frozen=True, slots=True)
class Grant:
    id: GrantId
    capability: str
    #: **Already normalized.** Never issued against raw input
    security_scope: SecurityScope
    granted_at: datetime
    expires_at: datetime | None = None
    remaining_uses: int | None = None
    #: **No value other than `user`.** Never build a path for Lumi to grant itself permission
    granted_by: Literal["user"] = "user"

    def is_valid_at(self, now: datetime) -> bool:
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return not (self.remaining_uses is not None and self.remaining_uses <= 0)


class GrantStore:
    """**A Tool can neither create nor remove a Grant** (docs/contracts/authority-matrix.md).

    `add` exists here because Phase 4a's permission-prompt UI calls it.
    **Static checks enforce that no Tool implementation ever imports `GrantStore`.**
    """

    __slots__ = ("_grants",)

    def __init__(self) -> None:
        self._grants: dict[GrantId, Grant] = {}

    def add(self, grant: Grant) -> None:
        self._grants[grant.id] = grant

    def find(self, capability: str, scope: SecurityScope, now: datetime) -> Grant | None:
        """**Only an exact scope match.** "Close enough" never passes (fail-closed)."""
        for grant in self._grants.values():
            if grant.capability != capability:
                continue
            if grant.security_scope != scope:
                continue
            if grant.is_valid_at(now):
                return grant
        return None

    def consume(self, grant_id: GrantId) -> None:
        """**Only ever mutated by decrementing.**"""
        grant = self._grants.get(grant_id)
        if grant is None or grant.remaining_uses is None:
            return
        self._grants[grant_id] = Grant(
            id=grant.id,
            capability=grant.capability,
            security_scope=grant.security_scope,
            granted_at=grant.granted_at,
            expires_at=grant.expires_at,
            remaining_uses=grant.remaining_uses - 1,
            granted_by=grant.granted_by,
        )
