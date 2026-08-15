"""Attention Arbiter — 「今なにをしているか」の単一の所有者。

設計 → docs/architecture/agent.md §1 / 状態機械 → docs/contracts/state-machines.md

## 守る不変条件

1. **`_foreground` が指す Activity は常にちょうど1つ**（Invariant 4）
2. **`running` を取れるのは foreground だけ**（背景に居るのは `cancelling` / `suspended`）
3. **Activity の状態遷移を実行できるのは Arbiter だけ**
4. **Activity の中断は `propose` / `interrupt` 以外の経路で起きない**
   （再生バッファのミュートは Activity 状態遷移ではないので対象外 → docs/architecture/audio.md）

## preempt の遷移順序

```
1. 旧 foreground → interrupt_requested
2. 即時効果（TTS 再生ミュート等）        ← Arbiter の外。VAD スレッドが同期的に行う
3. 新 Activity → accepted
4. _foreground を新に切り替える          ★ ここで Invariant 4 が満たされ続ける
5. 新 → running / 旧 → cancelling（background）
6. 旧は background で子の停止を待つ
```

**4 より前に旧を `cancelling` にしない。** 一瞬 `running` が0個になり Invariant 4 が破れる。
**`_foreground` の切り替えは、旧の子の停止を待たない。** 待つと barge-in が遅れる。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from lumi import logging as lumi_logging
from lumi.kernel.activity import (
    Activity,
    ActivityKind,
    ActivityProposal,
    ActivityState,
    can_preempt,
    new_idle_activity,
)
from lumi.kernel.cancellation import Cancellable, Cancellation, CancelToken
from lumi.kernel.event import DomainEventDraft, EventBus, activity_stream
from lumi.kernel.ids import ActivityId, JobId, new_activity_id, new_correlation_id
from lumi.kernel.job import Job

log = lumi_logging.get_logger(__name__)

#: DeferredQueue の保持期間〔Provisional〕。
#: **30分前に話しかけたかったことを今実行されるのは不気味である。**
DEFERRED_TTL: Final = timedelta(minutes=10)

#: `cooperative` な仕事が止まるのを待つ時間〔Provisional〕。
#: これを過ぎたら待たずに切り離す。**待ち続けると barge-in が壊れる。**
COOPERATIVE_GRACE_S: Final = 0.5


@dataclass(frozen=True, slots=True)
class Accepted:
    activity: Activity


@dataclass(frozen=True, slots=True)
class Deferred:
    retry_after: timedelta


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: str


ProposalOutcome = Accepted | Deferred | Rejected


@dataclass(frozen=True, slots=True)
class InterruptResult:
    """**何が止まり、何が abandoned になったかを返す。** Inspector に出してデバッグ可能にする。"""

    activity_id: ActivityId
    final_state: ActivityState
    reason: str
    stopped: tuple[str, ...] = ()
    abandoned: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InferenceLease:
    """Job が推論してよい間だけ有効。**foreground が推論を要求すると revoke される。**"""

    job_id: JobId
    token: CancelToken


@dataclass(frozen=True, slots=True)
class _DeferredEntry:
    proposal: ActivityProposal
    offered_at: datetime


class DeferredQueue:
    """受理できなかった提案を、TTL 付きで保持する。**Arbiter が所有する。**"""

    __slots__ = ("_entries", "_ttl")

    def __init__(self, *, ttl: timedelta = DEFERRED_TTL) -> None:
        #: キーは (kind, intent)。**同一 kind × intent は1件**（新しい方で置換）
        self._entries: dict[tuple[ActivityKind, str], _DeferredEntry] = {}
        self._ttl = ttl

    def offer(self, proposal: ActivityProposal, now: datetime) -> Deferred:
        self._entries[(proposal.kind, proposal.intent)] = _DeferredEntry(proposal, now)
        return Deferred(retry_after=self._ttl)

    def take_ready(self, now: datetime) -> list[ActivityProposal]:
        """期限内のものを取り出す。**期限切れは黙って捨てる**（それが TTL の意味）。"""
        ready: list[ActivityProposal] = []
        for (kind, intent), entry in self._entries.items():
            if now - entry.offered_at > self._ttl:
                log.info("arbiter.deferred_expired", kind=kind.value, intent=intent)
            else:
                ready.append(entry.proposal)
        self._entries.clear()
        return ready

    def __len__(self) -> int:
        return len(self._entries)


class AttentionArbiter:
    """**Activity の状態遷移を実行してよい唯一の場所。**"""

    __slots__ = (
        "_activities",
        "_bus",
        "_clock",
        "_deferred",
        "_foreground",
        "_leases",
        "_pending",
    )

    def __init__(
        self,
        bus: EventBus,
        *,
        clock: Callable[[], datetime] | None = None,
        deferred_ttl: timedelta = DEFERRED_TTL,
    ) -> None:
        self._bus = bus
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._activities: dict[ActivityId, Activity] = {}
        self._deferred = DeferredQueue(ttl=deferred_ttl)
        self._leases: dict[JobId, InferenceLease] = {}
        self._foreground: ActivityId | None = None
        #: 背景で走る「旧 Activity の後始末」。**参照を保持しないと GC で消える**
        self._pending: set[asyncio.Task[InterruptResult]] = set()

    # ── 起動 ──────────────────────────────────────────────

    async def start(self) -> Activity:
        """idle Activity を `running` で生成し、foreground にする。

        **`current()` が None を返さない**のは、ここが起動時に必ず呼ばれるため。
        """
        if self._foreground is not None:
            return self.current()
        idle = new_idle_activity(new_correlation_id())
        self._activities[idle.id] = idle
        self._foreground = idle.id
        await self._publish_started(idle)
        return idle

    # ── 参照 ──────────────────────────────────────────────

    def current(self) -> Activity:
        """**None を返さない。** 呼び出し側に null チェックを書かせない。"""
        if self._foreground is None:
            raise RuntimeError("Arbiter が start() されていない")
        return self._activities[self._foreground]

    def get(self, activity_id: ActivityId) -> Activity:
        return self._activities[activity_id]

    @property
    def deferred_count(self) -> int:
        return len(self._deferred)

    # ── 提案 ──────────────────────────────────────────────

    async def propose(self, proposal: ActivityProposal) -> ProposalOutcome:
        current = self.current()

        if not can_preempt(proposal.priority, current):
            if proposal.deferrable:
                return self._deferred.offer(proposal, self._clock())
            return Rejected(reason="busy")

        new = Activity(
            id=new_activity_id(),
            kind=proposal.kind,
            actor=proposal.actor,
            intent=proposal.intent,
            correlation_id=proposal.correlation_id,
            deadline=proposal.deadline,
        )
        self._activities[new.id] = new

        # 1. 旧 foreground を止めにかかる（idle は「止める」のではなく退避する）
        interrupting = current.kind is not ActivityKind.IDLE
        if interrupting:
            current._apply(ActivityState.INTERRUPT_REQUESTED)

        # 3. 新 → accepted
        new._apply(ActivityState.ACCEPTED)

        # 4. ★ foreground の切り替え。ここで Invariant 4 が満たされ続ける
        self._foreground = new.id

        # 5. 新 → running / 旧 → cancelling（または idle は suspended）
        new._apply(ActivityState.RUNNING)
        await self._publish_started(new)

        if interrupting:
            current._apply(ActivityState.CANCELLING)
            # 6. **旧の子の停止を待たない。** 待つと barge-in が遅れる
            task = asyncio.create_task(
                self._finish_cancelling(current, reason=f"preempted_by:{proposal.kind.value}")
            )
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        else:
            current._apply(ActivityState.SUSPENDED)

        return Accepted(new)

    def take_deferred(self) -> list[ActivityProposal]:
        """foreground が idle に戻ったとき等に、保留していた提案を取り出す。

        **取り出すだけで、ここでは再提案しない。** 呼び出し側（Deliberative Loop）が
        改めて `propose()` する。Arbiter が勝手に発火させると、
        「なぜ今それを言ったのか」の起点が Arbiter の中に隠れてしまう。
        """
        return self._deferred.take_ready(self._clock())

    # ── 中断 ──────────────────────────────────────────────

    async def interrupt(self, reason: str) -> InterruptResult:
        """foreground を中断し、idle に戻す。**Activity 中断の唯一の入口**（Invariant 4）。

        「音が止まる」はここを通らない。VAD スレッドが同期的に `mute_flag` を立てる
        （docs/architecture/audio.md）。**ユーザーが体感するのは前者。**
        """
        current = self.current()
        if current.kind is ActivityKind.IDLE:
            # idle への cancel は no-op（state-machines.md）
            return InterruptResult(activity_id=current.id, final_state=current.state, reason=reason)

        current._apply(ActivityState.INTERRUPT_REQUESTED)
        await self._to_idle()
        current._apply(ActivityState.CANCELLING)
        return await self._finish_cancelling(current, reason=reason)

    async def _finish_cancelling(self, activity: Activity, *, reason: str) -> InterruptResult:
        activity.cancel_token.fire(reason)
        stopped, abandoned = await self._stop_cancellables(activity)

        final = ActivityState.ABANDONED if abandoned else ActivityState.CANCELLED
        activity._apply(final)
        await self._publish_ended(activity, reason=reason)

        result = InterruptResult(
            activity_id=activity.id,
            final_state=final,
            reason=reason,
            stopped=tuple(stopped),
            abandoned=tuple(abandoned),
        )
        log.info(
            "arbiter.interrupted",
            activity_id=str(activity.id),
            kind=activity.kind.value,
            final_state=final.value,
            reason=reason,
            stopped=list(stopped),
            abandoned=list(abandoned),
        )
        return result

    async def _stop_cancellables(self, activity: Activity) -> tuple[list[str], list[str]]:
        """契約ごとに止める。

        | 契約 | やること |
        |---|---|
        | `hard` | `kill()` を即座に呼ぶ |
        | `cooperative` | token を fire し、猶予時間だけ待つ |
        | `non_cancellable` | **待たずに切り離す** → abandoned |

        **猶予を過ぎても止まらない `cooperative` も abandoned にする。**
        契約違反の兆候なので警告を出すが、待ち続けて barge-in を壊す方が悪い。
        """
        stopped: list[str] = []
        abandoned: list[str] = []
        waiting: list[Cancellable] = []

        for item in activity.cancellables:
            if item.contract is Cancellation.NON_CANCELLABLE:
                abandoned.append(item.label)
                continue
            item.cancel_token.fire("activity_cancelled")
            if item.contract is Cancellation.HARD:
                await self._kill(item, stopped, abandoned)
            else:
                waiting.append(item)

        for item in waiting:
            try:
                async with asyncio.timeout(COOPERATIVE_GRACE_S):
                    await item.finished.wait()
                stopped.append(item.label)
            except TimeoutError:
                log.warning(
                    "arbiter.cooperative_timeout",
                    activity_id=str(activity.id),
                    label=item.label,
                    grace_s=COOPERATIVE_GRACE_S,
                )
                abandoned.append(item.label)

        return stopped, abandoned

    async def _kill(self, item: Cancellable, stopped: list[str], abandoned: list[str]) -> None:
        assert item.kill is not None  # Cancellable.__post_init__ が保証する
        try:
            await item.kill()
        except Exception:
            # 止められなかった。**止まったことにしない。**
            log.exception("arbiter.kill_failed", label=item.label)
            abandoned.append(item.label)
        else:
            stopped.append(item.label)

    # ── 正常終了 ───────────────────────────────────────────

    async def complete(self, activity_id: ActivityId, *, failed: bool = False) -> None:
        """正常終了（または異常終了）。foreground なら idle に戻す。"""
        activity = self._activities[activity_id]
        activity._apply(ActivityState.COMPLETING)
        if self._foreground == activity_id:
            await self._to_idle()
        activity._apply(ActivityState.FAILED if failed else ActivityState.COMPLETED)
        await self._publish_ended(activity, reason="failed" if failed else "completed")

    async def _to_idle(self) -> None:
        """foreground を idle に戻す。**idle は消滅しない**ので、探して起こすだけ。"""
        idle = self._find_idle()
        self._foreground = idle.id
        if idle.state is ActivityState.SUSPENDED:
            idle._apply(ActivityState.RUNNING)

    def _find_idle(self) -> Activity:
        for activity in self._activities.values():
            if activity.kind is ActivityKind.IDLE:
                return activity
        raise RuntimeError("idle Activity が存在しない（start() されていない）")

    # ── 推論資源の調停 ──────────────────────────────────────

    @asynccontextmanager
    async def inference_lease(self, job: Job) -> AsyncIterator[InferenceLease]:
        """`uses_inference` な Job は、これを取ってから推論する。

        これが無いと、Reflection Job の推論が会話の LLM 初トークンを待たせ、
        **レイテンシ SLO（p95 2.0秒）を直撃する**（ADR-018）。
        """
        lease = InferenceLease(job_id=job.id, token=job.cancel_token)
        self._leases[job.id] = lease
        try:
            yield lease
        finally:
            self._leases.pop(job.id, None)

    def request_inference(self, activity_id: ActivityId) -> None:
        """**foreground Activity が推論を始めるときに呼ぶ。** 既存の Job lease を revoke する。

        foreground でない Activity からの要求は無視する（背景の仕事が
        Job を追い出せると、優先順位が意味を失う）。
        """
        if self._foreground != activity_id:
            return
        for lease in list(self._leases.values()):
            lease.token.fire("inference_revoked")

    # ── DomainEvent ───────────────────────────────────────

    async def _publish_started(self, activity: Activity) -> None:
        await self._publish(activity, "ActivityStarted", {})

    async def _publish_ended(self, activity: Activity, *, reason: str) -> None:
        await self._publish(
            activity, "ActivityEnded", {"final_state": activity.state.value, "reason": reason}
        )

    async def _publish(self, activity: Activity, event_type: str, extra: dict[str, object]) -> None:
        """**payload に発話本文を入れない。**

        `intent` は「何をしようとしているか」の識別子（`user_speech` など）であって、
        ユーザーが何と言ったかではない。会話内容の永続化は Phase 2
        （`contracts/privacy.md` を書いてから）→ `lumi/storage/sqlite.py`
        """
        await self._bus.publish(
            DomainEventDraft(
                stream_key=activity_stream(activity.id),
                type=event_type,
                payload={
                    "kind": activity.kind.value,
                    "actor": activity.actor.value,
                    "intent": activity.intent,
                    "priority": activity.priority,
                    **extra,
                },
                correlation_id=activity.correlation_id,
            )
        )
