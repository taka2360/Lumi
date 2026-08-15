"""Activity — 「今 Lumi がしていること」の単位。

状態機械の唯一の定義場所 → docs/contracts/state-machines.md
priority の値 → docs/architecture/agent.md §1（決定 → ADR-024）

**状態遷移を実行してよいのは Attention Arbiter だけ。** ここには
「その遷移が許されるか」の表と、不正な遷移で落ちる仕組みだけを置く。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from lumi.kernel.cancellation import Cancellable, CancelToken
from lumi.kernel.ids import ActivityId, CorrelationId, new_activity_id


class ActivityKind(StrEnum):
    CONVERSATION = "conversation"
    AUTONOMOUS = "autonomous"
    TASK = "task"
    GAME = "game"
    IDLE = "idle"


class Actor(StrEnum):
    """**誰の意思で始まったか。** Policy の引数でもある（docs/architecture/permission.md）。

    「ユーザーが頼んだファイル読み取り」と「Lumi が勝手にするファイル読み取り」は
    別の行為である。当たり前のことを、型で表現する。
    """

    USER_INITIATED = "user_initiated"
    SELF_INITIATED = "self_initiated"
    SCHEDULED = "scheduled"
    #: idle Activity と Job。**L0 のツールしか使えない**
    SYSTEM = "system"


class ActivityState(StrEnum):
    PROPOSED = "proposed"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    ACCEPTED = "accepted"
    #: **これが foreground。同時に1つだけ**（Invariant 4）
    RUNNING = "running"
    #: **idle 専用。** 他の Activity が foreground の間、idle が取る
    SUSPENDED = "suspended"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPT_REQUESTED = "interrupt_requested"
    CANCELLING = "cancelling"
    #: 全ての子が停止または完了した
    CANCELLED = "cancelled"
    #: `non_cancellable` な子の完了を**待たずに切り離した**
    ABANDONED = "abandoned"


# ── priority（ADR-024。値は Provisional）───────────────────────
# 10 刻みにしてあるのは、後から間に挿入できるようにするため。

_PRIORITY: Final[dict[ActivityKind, int]] = {
    ActivityKind.IDLE: 0,
    ActivityKind.AUTONOMOUS: 30,
    ActivityKind.TASK: 50,
    ActivityKind.GAME: 60,
    ActivityKind.CONVERSATION: 100,
}

_INTERRUPTIBLE_AT: Final[dict[ActivityKind, int]] = {
    #: すべてに割り込まれる
    ActivityKind.IDLE: 0,
    #: ユーザー発話にだけ割り込まれる
    ActivityKind.AUTONOMOUS: 100,
    ActivityKind.TASK: 100,
    ActivityKind.GAME: 100,
    #: **新しいユーザー発話に割り込まれる（barge-in）**
    ActivityKind.CONVERSATION: 100,
}


def priority_of(kind: ActivityKind, actor: Actor) -> int:
    """priority は**表から決まる。** 提案者（LLM・Stage・Extension）は渡せない。

    「この自律行動は緊急です」と主張する経路を作らないため（Invariant 1 を Arbiter 側でも守る）。

    現在の表は `kind` だけで決まるが、**`actor` を引数に残してある。**
    priority は「kind と actor という Core が決める事実から導く」ものであり、
    その導出であることをシグネチャで示す（ADR-024）。
    """
    del actor  # 現在の表では使わない。上記の理由でシグネチャに残す
    return _PRIORITY[kind]


def interruptible_at_of(kind: ActivityKind) -> int:
    """この値**以上**の priority を持つ提案に割り込まれる。"""
    return _INTERRUPTIBLE_AT[kind]


def can_preempt(proposal_priority: int, current: Activity) -> bool:
    """割り込んでよいか。**`>` ではなく `>=`。**

    barge-in は「会話が会話を割り込む」＝**同一 priority の preempt** であり、
    `>` にすると成立しない。**同じ強さのものは、新しい方が勝つ。**
    """
    return proposal_priority >= current.interruptible_at


class InvalidTransition(RuntimeError):
    """許されていない状態遷移。**握りつぶさない**（状態機械が壊れている証拠）。"""


#: 図 → docs/contracts/state-machines.md「Activity 状態機械」
_ALLOWED: Final[dict[ActivityState, frozenset[ActivityState]]] = {
    ActivityState.PROPOSED: frozenset(
        {ActivityState.REJECTED, ActivityState.DEFERRED, ActivityState.ACCEPTED}
    ),
    ActivityState.REJECTED: frozenset(),
    #: DeferredQueue から再提案されると accepted に進む
    ActivityState.DEFERRED: frozenset({ActivityState.ACCEPTED, ActivityState.REJECTED}),
    ActivityState.ACCEPTED: frozenset({ActivityState.RUNNING}),
    ActivityState.RUNNING: frozenset(
        {
            ActivityState.COMPLETING,
            ActivityState.INTERRUPT_REQUESTED,
            ActivityState.SUSPENDED,
        }
    ),
    #: idle が foreground に戻る / 起動時から一度も foreground を離れない場合もある
    ActivityState.SUSPENDED: frozenset({ActivityState.RUNNING}),
    ActivityState.COMPLETING: frozenset({ActivityState.COMPLETED, ActivityState.FAILED}),
    ActivityState.COMPLETED: frozenset(),
    ActivityState.FAILED: frozenset(),
    ActivityState.INTERRUPT_REQUESTED: frozenset({ActivityState.CANCELLING}),
    ActivityState.CANCELLING: frozenset({ActivityState.CANCELLED, ActivityState.ABANDONED}),
    ActivityState.CANCELLED: frozenset(),
    ActivityState.ABANDONED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ActivityProposal:
    """Arbiter への提案。**priority を持たない**（ADR-024）。"""

    kind: ActivityKind
    actor: Actor
    #: 何をしようとしているか。`DeferredQueue` の重複判定に使う（同一 kind × intent は1件）
    intent: str
    correlation_id: CorrelationId
    #: 受理できないとき、後で再提案してよいか。自律は True、ユーザー発話は False
    deferrable: bool = False
    deadline: datetime | None = None

    @property
    def priority(self) -> int:
        return priority_of(self.kind, self.actor)


@dataclass(slots=True)
class Activity:
    """**状態を書き換えてよいのは Attention Arbiter だけ**（`_apply` 経由）。"""

    id: ActivityId
    kind: ActivityKind
    actor: Actor
    intent: str
    correlation_id: CorrelationId
    cancel_token: CancelToken = field(default_factory=CancelToken)
    deadline: datetime | None = None
    parent: ActivityId | None = None
    children: list[ActivityId] = field(default_factory=list)
    #: この Activity にぶら下がる「止められる仕事」。Tool 実行 / LLM ストリーム / TTS 生成
    cancellables: list[Cancellable] = field(default_factory=list)
    _state: ActivityState = ActivityState.PROPOSED

    @property
    def state(self) -> ActivityState:
        return self._state

    @property
    def priority(self) -> int:
        return priority_of(self.kind, self.actor)

    @property
    def interruptible_at(self) -> int:
        return interruptible_at_of(self.kind)

    def _apply(self, new_state: ActivityState) -> None:
        """**Attention Arbiter からのみ呼ぶ。** 他から呼ばれていないことをテストで検査する。"""
        if new_state not in _ALLOWED[self._state]:
            raise InvalidTransition(f"{self.kind}: {self._state} → {new_state} は許されていない")
        if new_state is ActivityState.SUSPENDED and self.kind is not ActivityKind.IDLE:
            # `suspended` は idle 専用。他が取ると「foreground でないが生きている」が増え、
            # Invariant 4 の「running はちょうど1つ」が意味を失う。
            raise InvalidTransition(f"{self.kind}: suspended を取れるのは idle だけ")
        self._state = new_state


def new_idle_activity(correlation_id: CorrelationId) -> Activity:
    """起動時の idle。**`proposed` / `accepted` を経ない唯一の例外**（state-machines.md）。

    最初から `running` で生成する。**これは遷移ではなく初期状態**なので、
    「状態遷移を実行するのは Arbiter だけ」とは矛盾しない。
    """
    return Activity(
        id=new_activity_id(),
        kind=ActivityKind.IDLE,
        actor=Actor.SYSTEM,
        intent="idle",
        correlation_id=correlation_id,
        _state=ActivityState.RUNNING,
    )
