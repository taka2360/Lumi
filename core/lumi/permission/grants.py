"""Grant — スコープ付きトークン。**ブール値ではない。**

設計 → docs/architecture/permission.md §5

これで表現できること:

- 「このセッション中、`C:\\Users\\yuasa\\Projects` 以下は読んでいい」
- 「この URL に1回だけアクセスしていい」
- 「今日いっぱい、このフォルダに書いていい」

> **UI に「全部許可」を置かない。** 置いた瞬間に、ユーザーは必ずそれを選ぶ。

**Phase 1 では Grant は作られない**（L0 のツールしか無く、`ask` に到達しない）。
経路と型だけを本番と同じにしておく。UI と発行は Phase 4a。
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
    #: **正規化済み。** 生の入力に対して発行しない
    security_scope: SecurityScope
    granted_at: datetime
    expires_at: datetime | None = None
    remaining_uses: int | None = None
    #: **`user` 以外に値がない。** Lumi が自分に権限を与える経路を作らない
    granted_by: Literal["user"] = "user"

    def is_valid_at(self, now: datetime) -> bool:
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return not (self.remaining_uses is not None and self.remaining_uses <= 0)


class GrantStore:
    """**Tool から Grant を作ることも消すこともできない**（docs/contracts/authority-matrix.md）。

    ここに `add` があるのは、Phase 4a の権限プロンプト UI が呼ぶため。
    **Tool 実装が `GrantStore` を import していないことを静的検査で縛る。**
    """

    __slots__ = ("_grants",)

    def __init__(self) -> None:
        self._grants: dict[GrantId, Grant] = {}

    def add(self, grant: Grant) -> None:
        self._grants[grant.id] = grant

    def find(self, capability: str, scope: SecurityScope, now: datetime) -> Grant | None:
        """**scope が一致するものだけ。** 「近い」では通さない（fail-closed）。"""
        for grant in self._grants.values():
            if grant.capability != capability:
                continue
            if grant.security_scope != scope:
                continue
            if grant.is_valid_at(now):
                return grant
        return None

    def consume(self, grant_id: GrantId) -> None:
        """**減算する形でのみ変更される。**"""
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
