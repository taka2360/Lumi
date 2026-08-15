"""Command — 「実行せよ」。**結果が要るもの。**

契約 → docs/contracts/event-model.md

```
結果が必要か？ → Yes: Command
              → No ↓
外から来たか？ → Yes: Signal
              → No : DomainEvent（Core が発行）
```

**迷ったら Command。** あとから Event に緩めるのは簡単だが、
Event で組んだ制御フローを Command に直すのは難しい。

AIRI は約60種の WS イベントでモジュールのライフサイクルを振り付けており、
「どこで止まったか」が追跡不能になっている。**Lumi はライフサイクルを
明示的な Command シーケンスにする。**
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from lumi.kernel.ids import CommandId, CorrelationId


@dataclass(frozen=True, slots=True)
class Command:
    """単一ハンドラに配送される。**戻り値を持ち、失敗しうる。**"""

    id: CommandId
    type: str
    payload: Mapping[str, Any]
    correlation_id: CorrelationId
    #: 副作用を持つ Command には**必須**（docs/architecture/recovery.md §3）
    idempotency_key: str | None = None


class CommandError(RuntimeError):
    """Command の配送に関する失敗。"""


class UnknownCommand(CommandError):
    """ハンドラが登録されていない。**推測で処理しない。**"""


class DuplicateHandler(CommandError):
    """同じ type に2つ目のハンドラを登録した。**「単一ハンドラ」が壊れる。**"""


class MissingIdempotencyKey(CommandError):
    """副作用を持つ Command に `idempotency_key` が無い。

    これが無いと、クラッシュ後に「同じ操作かどうか」が判定できず、
    Crash Recovery（Phase 4a）が成立しない。**Phase 1 の時点で塞いでおく。**
    """


CommandHandler = Callable[[Command], Awaitable[Any]]


class CommandBus:
    """type ごとに単一ハンドラ。

    **キューを持たない。** 呼び出し側が `await` するので、backpressure は
    「待たされる」という形で自然にかかる。キューを挟むと、
    「結果が要る」という Command の性質と衝突する。
    """

    __slots__ = ("_handlers", "_side_effecting")

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}
        self._side_effecting: set[str] = set()

    def register(
        self, command_type: str, handler: CommandHandler, *, has_side_effect: bool = False
    ) -> None:
        """`has_side_effect=True` の type は、`idempotency_key` 無しでは配送されない。"""
        if command_type in self._handlers:
            raise DuplicateHandler(command_type)
        self._handlers[command_type] = handler
        if has_side_effect:
            self._side_effecting.add(command_type)

    async def dispatch(self, command: Command) -> Any:
        handler = self._handlers.get(command.type)
        if handler is None:
            raise UnknownCommand(command.type)
        if command.type in self._side_effecting and command.idempotency_key is None:
            raise MissingIdempotencyKey(command.type)
        return await handler(command)
