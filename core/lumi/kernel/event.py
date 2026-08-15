"""Signal と DomainEvent、そして EventBus。

契約の唯一の定義場所 → docs/contracts/event-model.md

> **外から来るものと、Core が「起こった」と宣言するものは、同じ型であってはならない。**

## 型で塞いでいること

| 塞ぐもの | やり方 |
|---|---|
| 外部が DomainEvent を直接書く | `Signal` が `stream_key` / `sequence_id` を**持たない** |
| 発行者が採番する | `DomainEventDraft` に `sequence_id` が**無い**。付けるのは `EventBus` だけ |

`Signal` を `DomainEvent` にするかどうか・どう表現するかは **Core が決める**。
Signal は素材であって、事実ではない。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from lumi import logging as lumi_logging
from lumi.kernel.ids import ActivityId, CorrelationId, EventId, new_event_id
from lumi.provenance import TrustLevel

log = lumi_logging.get_logger(__name__)


# ── stream_key ────────────────────────────────────────────────
# 「何についての Event か」から機械的に決まる。**「順序保証が要るか?」を判断させない**
# （その判断は間違えやすい → docs/contracts/event-model.md）。


def activity_stream(activity_id: ActivityId) -> str:
    return f"activity:{activity_id}"


def session_stream(session_id: str) -> str:
    return f"session:{session_id}"


def memory_stream(subject: str) -> str:
    return f"memory:{subject}"


def world_stream(facet_key: str) -> str:
    return f"world:{facet_key}"


PERMISSION_STREAM = "permission"
AUTONOMY_STREAM = "autonomy"


@dataclass(frozen=True, slots=True)
class Signal:
    """外部（Shell / Stage / Extension / Widget）から Core に届く通知。

    **これは事実ではない。** Core が解釈して初めて DomainEvent になる。
    Stage は「予算を減らせ」とは言わない。「ユーザーがうるさいと言った」と伝えるだけである。
    """

    #: 誰が送ったか。認証済みの peer identity
    source_id: str
    type: str
    payload: Mapping[str, Any]
    received_at: datetime
    #: 送出元の信頼度から決まる。ここから派生物に伝播する
    trust_level: TrustLevel

    # stream_key も sequence_id も**持たない**。これが型による保証（Invariant 6）。


@dataclass(frozen=True, slots=True)
class DomainEventDraft:
    """発行者が作るもの。**`sequence_id` を持たない。**

    別の型にしてあるのは、発行者が採番に触れないことをコンパイル時に保証するため。
    """

    stream_key: str
    type: str
    payload: Mapping[str, Any]
    correlation_id: CorrelationId
    #: これを引き起こした Command / Signal / DomainEvent
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Core が「起きた」と宣言する事実。**過去形で名づける。**"""

    id: EventId
    stream_key: str
    #: stream_key 内での単調増加。ギャップなし。**代入するのは EventBus だけ**
    sequence_id: int
    causation_id: str | None
    correlation_id: CorrelationId
    type: str
    payload: Mapping[str, Any]
    occurred_at: datetime


class EventStore(Protocol):
    """DomainEvent の永続化。実装は `lumi.storage`（依存の向きを逆にするための Protocol）。"""

    async def append(
        self, event_id: EventId, draft: DomainEventDraft, occurred_at: datetime
    ) -> int:
        """`stream_key` の次の `sequence_id` を採番し、**同一トランザクションで**永続化して返す。

        **採番と永続化を分けてはならない。** 分けると、その間のクラッシュでギャップが生まれ、
        「ギャップ = 真の異常」という前提が崩れて、検出そのものが意味を失う。
        """
        ...


Subscriber = Callable[[DomainEvent], Awaitable[None]]


class SequenceError(RuntimeError):
    """欠番または逆転を検出した。**黙って処理しない。**"""


class SequenceChecker:
    """消費側のためのもの。stream ごとに連番を検査する。

    **欠番を無視しないための道具。** 無視すると、EventBus の採番が壊れていることに
    誰も気づかないまま Lumi が動き続ける。
    """

    __slots__ = ("_last",)

    def __init__(self) -> None:
        self._last: dict[str, int] = {}

    def check(self, event: DomainEvent) -> None:
        previous = self._last.get(event.stream_key)
        expected = 1 if previous is None else previous + 1
        if event.sequence_id != expected:
            raise SequenceError(
                f"{event.stream_key}: {expected} を期待したが {event.sequence_id} が来た"
            )
        self._last[event.stream_key] = event.sequence_id


class EventBus:
    """**唯一の採番者。** stream_key ごとに直列化する。

    複数のコルーチンが同じ stream に発行する状況は必ず発生するため、
    「発行者が気をつける」ではなく Bus 側で直列化する。
    """

    __slots__ = ("_clock", "_locks", "_store", "_subscribers")

    def __init__(
        self,
        store: EventStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribers: list[Subscriber] = []

    def subscribe(self, handler: Subscriber) -> Callable[[], None]:
        """購読する。戻り値を呼ぶと解除される。"""
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    async def publish(self, draft: DomainEventDraft) -> DomainEvent:
        """採番 → 永続化 → 配送。**この順序を変えない。**

        配送してから永続化すると、購読側が観測した事実が永続化されないまま
        クラッシュしうる（購読側の状態と履歴が食い違う）。
        """
        lock = self._locks.setdefault(draft.stream_key, asyncio.Lock())
        async with lock:
            event_id = new_event_id()
            occurred_at = self._clock()
            sequence_id = await self._store.append(event_id, draft, occurred_at)
            event = DomainEvent(
                id=event_id,
                stream_key=draft.stream_key,
                sequence_id=sequence_id,
                causation_id=draft.causation_id,
                correlation_id=draft.correlation_id,
                type=draft.type,
                payload=draft.payload,
                occurred_at=occurred_at,
            )
        await self._dispatch(event)
        return event

    async def _dispatch(self, event: DomainEvent) -> None:
        for handler in list(self._subscribers):
            try:
                await handler(event)
            except Exception:
                # **1つの購読者の失敗で Bus を止めない。** ただし黙って捨てない。
                log.exception(
                    "event.subscriber_failed",
                    stream_key=event.stream_key,
                    type=event.type,
                    sequence_id=event.sequence_id,
                )
