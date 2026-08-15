"""Canonicalizer / BindVerifier / ResultVerifier — **すべて Kernel 所有。**

契約 → docs/contracts/tool-execution.md / 型 → docs/interfaces/tool.md

## なぜ Tool 側に置かないのか

`Tool.bind` は Tool が実装する。もし Tool が（悪意または実装ミスで）scope とは違う対象の
Handle を返したら、`execute` はその対象を操作してしまう。

**`BindVerifier` は Kernel 所有で、Tool が返した Handle が本当に scope を指しているかを
独立に検証する。** これが無いと、契約は「Tool を信頼する」に退化する。

## Phase 1 の範囲

`character` lane のみ。**Phase 4a で `fs`、4b で `browser`、4c で `input` が入る。**
本実装（realpath / traversal / UNC / IDN / リダイレクト事前解決）は Phase 4a。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from lumi.permission.scope import Handle, ScopeLane, SecurityScope


class CanonicalizationError(ValueError):
    """正規化に失敗した。**`deny` になる**（「よく分からないので通す」経路を作らない）。"""


class BindVerificationError(RuntimeError):
    """Handle が scope を指していない。**`execute` しない。**"""


class ResultVerificationError(RuntimeError):
    """Class B が scope 外を操作したと申告した。**結果を破棄する**（副作用は防げない）。"""


class Canonicalizer(Protocol):
    lane: ScopeLane

    def canonicalize(self, raw_input: Mapping[str, Any]) -> SecurityScope:
        """失敗したら `CanonicalizationError`（fail-closed）。"""
        ...


class BindVerifier(Protocol):
    """Class A。**execute の前に検証する。副作用を起こさせない。**"""

    lane: ScopeLane

    def verify(self, scope: SecurityScope, handle: Handle) -> None:
        """不一致なら `BindVerificationError`。"""
        ...


class ResultVerifier(Protocol):
    """Class B。**invoke の後。副作用は既に起きている可能性がある。**"""

    lane: ScopeLane

    def verify(self, scope: SecurityScope, acted_on: str) -> None:
        """scope 外なら `ResultVerificationError`。結果を破棄し `denied` として記録する。"""
        ...


#: `character` lane の対象は Lumi 自身のキャラクター1体しかない。
CHARACTER_SELF = "character:self"


class CharacterCanonicalizer:
    """`character` lane。**対象は常に Lumi 自身。**

    表情の種類（`emotion`）を `canonical` に入れない。scope は「**何に対する権限か**」であって
    「何をするか」ではない。emotion まで scope に含めると、Grant が
    「happy にはしてよいが sad はだめ」という粒度になり、意味を持たない。
    """

    lane = ScopeLane.CHARACTER

    def canonicalize(self, raw_input: Mapping[str, Any]) -> SecurityScope:
        """対象を正規化し、**操作パラメータは scope に載せて運ぶ。**

        `execute` は `handle.scope` しか読まない（生入力を再解決しない = TOCTOU 対策）ので、
        引数は不変な scope に載せる必要がある。

        **パラメータの妥当性は検証しない。** それはドメイン知識であり Tool の仕事である。
        Canonicalizer がやるのは「**どの対象に対する操作か**」を確定させることだけ。
        """
        target = raw_input.get("target", "self")
        if target != "self":
            # 他のキャラクターという概念は無い。**知らない対象は通さない。**
            raise CanonicalizationError(f"未知の対象: {target!r}")
        return SecurityScope(
            lane=ScopeLane.CHARACTER,
            canonical=CHARACTER_SELF,
            metadata={key: value for key, value in raw_input.items() if key != "target"},
        )


class CharacterBindVerifier:
    """`character` lane。

    **正直に書くと、この lane で検証できる実体は薄い。** fs の `fstat`、input の
    `WindowFromPoint` に相当するものが無く、確かめられるのは
    「Handle が主張する scope が、Policy の検査した scope と同一か」だけである。

    それでも置くのは、**経路を本番と同じにするため**（Phase 1 の目的）。
    ここが Kernel 所有であることが、Phase 4a で `fs` を足すときの前提になる。
    """

    lane = ScopeLane.CHARACTER

    def verify(self, scope: SecurityScope, handle: Handle) -> None:
        if handle.scope != scope:
            raise BindVerificationError(
                f"handle の scope が違う: {handle.scope.canonical!r} != {scope.canonical!r}"
            )
