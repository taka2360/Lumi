"""Attention Arbiter。

**docs/contracts/state-machines.md テスト 1〜3c / 5 / 10。**
**docs/architecture/agent.md テスト 1〜4e。**
すべて **LLM を呼ばずに**テストできる。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from lumi.kernel import arbiter as arbiter_module
from lumi.kernel.activity import (
    ActivityKind,
    ActivityProposal,
    ActivityState,
    Actor,
)
from lumi.kernel.arbiter import Accepted, AttentionArbiter, Deferred, Rejected
from lumi.kernel.cancellation import Cancellable, Cancellation
from lumi.kernel.event import EventBus
from lumi.kernel.ids import new_correlation_id, new_job_id
from lumi.kernel.job import Job, JobKind
from lumi.storage.events import SqliteEventStore
from lumi.storage.sqlite import Database


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database.open(":memory:")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
async def arbiter(database: Database) -> AttentionArbiter:
    instance = AttentionArbiter(EventBus(SqliteEventStore(database)))
    await instance.start()
    return instance


def user_speech() -> ActivityProposal:
    return ActivityProposal(
        kind=ActivityKind.CONVERSATION,
        actor=Actor.USER_INITIATED,
        intent="user_speech",
        correlation_id=new_correlation_id(),
    )


def autonomous(intent: str = "small_talk") -> ActivityProposal:
    return ActivityProposal(
        kind=ActivityKind.AUTONOMOUS,
        actor=Actor.SELF_INITIATED,
        intent=intent,
        correlation_id=new_correlation_id(),
        deferrable=True,
    )


async def settle(instance: AttentionArbiter) -> None:
    """背景の後始末タスクを待つ。**本番では待たない**（待つと barge-in が遅れる）。"""
    if instance._pending:
        await asyncio.gather(*list(instance._pending))


def running_count(instance: AttentionArbiter) -> int:
    return sum(
        1 for activity in instance._activities.values() if activity.state is ActivityState.RUNNING
    )


# ── foreground の不変条件 ────────────────────────────────────


async def test_current_returns_idle_after_start(arbiter: AttentionArbiter) -> None:
    current = arbiter.current()
    assert current.kind is ActivityKind.IDLE
    assert current.state is ActivityState.RUNNING


def test_current_before_start_is_an_error(database: Database) -> None:
    instance = AttentionArbiter(EventBus(SqliteEventStore(database)))
    with pytest.raises(RuntimeError):
        instance.current()


async def test_exactly_one_running_activity_through_a_preempt(
    arbiter: AttentionArbiter,
) -> None:
    """**Invariant 4。** preempt の途中でも `running` は1つ。"""
    assert running_count(arbiter) == 1

    first = await arbiter.propose(user_speech())
    assert isinstance(first, Accepted)
    assert running_count(arbiter) == 1
    assert arbiter.current().id == first.activity.id

    second = await arbiter.propose(user_speech())
    assert isinstance(second, Accepted)
    # 旧はまだ background で cancelling。ここが「窓が開いている」瞬間
    assert running_count(arbiter) == 1
    assert arbiter.current().id == second.activity.id
    assert arbiter.get(first.activity.id).state is ActivityState.CANCELLING

    await settle(arbiter)
    assert running_count(arbiter) == 1
    assert arbiter.get(first.activity.id).state is ActivityState.CANCELLED


async def test_idle_is_suspended_while_another_activity_is_foreground(
    arbiter: AttentionArbiter,
) -> None:
    idle_id = arbiter.current().id
    await arbiter.propose(user_speech())
    assert arbiter.get(idle_id).state is ActivityState.SUSPENDED


async def test_foreground_returns_to_idle_after_complete(arbiter: AttentionArbiter) -> None:
    idle_id = arbiter.current().id
    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)

    await arbiter.complete(accepted.activity.id)

    assert arbiter.current().id == idle_id
    assert arbiter.current().state is ActivityState.RUNNING
    assert arbiter.get(accepted.activity.id).state is ActivityState.COMPLETED


async def test_failed_activity_ends_as_failed(arbiter: AttentionArbiter) -> None:
    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)
    await arbiter.complete(accepted.activity.id, failed=True)
    assert arbiter.get(accepted.activity.id).state is ActivityState.FAILED


# ── 提案の判定 ──────────────────────────────────────────────


async def test_autonomous_is_accepted_while_idle(arbiter: AttentionArbiter) -> None:
    outcome = await arbiter.propose(autonomous())
    assert isinstance(outcome, Accepted)


async def test_autonomous_is_deferred_during_a_conversation(arbiter: AttentionArbiter) -> None:
    await arbiter.propose(user_speech())
    outcome = await arbiter.propose(autonomous())
    assert isinstance(outcome, Deferred)
    assert arbiter.deferred_count == 1


async def test_non_deferrable_proposal_is_rejected_when_busy(arbiter: AttentionArbiter) -> None:
    await arbiter.propose(user_speech())
    outcome = await arbiter.propose(
        ActivityProposal(
            kind=ActivityKind.AUTONOMOUS,
            actor=Actor.SELF_INITIATED,
            intent="urgent",
            correlation_id=new_correlation_id(),
            deferrable=False,
        )
    )
    assert isinstance(outcome, Rejected)


async def test_user_speech_preempts_an_autonomous_activity(arbiter: AttentionArbiter) -> None:
    started = await arbiter.propose(autonomous())
    assert isinstance(started, Accepted)

    outcome = await arbiter.propose(user_speech())
    assert isinstance(outcome, Accepted)
    await settle(arbiter)
    assert arbiter.get(started.activity.id).state is ActivityState.CANCELLED


# ── DeferredQueue ─────────────────────────────────────────


async def test_deferred_proposals_are_deduplicated(arbiter: AttentionArbiter) -> None:
    """**同一 kind × intent は1件**（新しい方で置換）。"""
    await arbiter.propose(user_speech())
    await arbiter.propose(autonomous("same"))
    await arbiter.propose(autonomous("same"))
    assert arbiter.deferred_count == 1


async def test_deferred_proposals_expire(database: Database) -> None:
    """**30分前に話しかけたかったことを今実行されるのは不気味である。**"""
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    clock = lambda: now  # noqa: E731
    instance = AttentionArbiter(
        EventBus(SqliteEventStore(database)), clock=clock, deferred_ttl=timedelta(minutes=10)
    )
    await instance.start()
    await instance.propose(user_speech())
    await instance.propose(autonomous())
    assert instance.deferred_count == 1

    now = now + timedelta(minutes=11)
    assert instance.take_deferred() == []


async def test_deferred_proposals_are_returned_while_fresh(database: Database) -> None:
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    instance = AttentionArbiter(EventBus(SqliteEventStore(database)), clock=lambda: now)
    await instance.start()
    await instance.propose(user_speech())
    await instance.propose(autonomous())
    assert len(instance.take_deferred()) == 1
    # 取り出したら消える（呼び出し側が改めて propose する）
    assert instance.deferred_count == 0


# ── 中断 ────────────────────────────────────────────────


async def test_interrupting_idle_is_a_no_op(arbiter: AttentionArbiter) -> None:
    result = await arbiter.interrupt("user_speech")
    assert result.final_state is ActivityState.RUNNING
    assert arbiter.current().kind is ActivityKind.IDLE


async def test_interrupt_stops_a_cooperative_child(arbiter: AttentionArbiter) -> None:
    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)
    item = Cancellable(id="llm", label="llm_stream", contract=Cancellation.COOPERATIVE)
    accepted.activity.cancellables.append(item)

    async def respond() -> None:
        await item.cancel_token.wait()
        item.mark_finished()

    task = asyncio.create_task(respond())

    result = await arbiter.interrupt("user_speech")
    await task

    assert result.final_state is ActivityState.CANCELLED
    assert result.stopped == ("llm_stream",)
    assert result.abandoned == ()


async def test_interrupt_kills_a_hard_child(arbiter: AttentionArbiter) -> None:
    """**TTS 再生の停止は `hard`。** barge-in の生命線。"""
    killed: list[str] = []

    async def kill() -> None:
        killed.append("playback")

    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)
    accepted.activity.cancellables.append(
        Cancellable(id="tts", label="playback", contract=Cancellation.HARD, kill=kill)
    )

    result = await arbiter.interrupt("user_speech")

    assert killed == ["playback"]
    assert result.stopped == ("playback",)
    assert result.final_state is ActivityState.CANCELLED


async def test_non_cancellable_child_makes_the_activity_abandoned(
    arbiter: AttentionArbiter,
) -> None:
    """**`abandoned` は正当な状態。** その結果は Lumi の文脈に入らない。"""
    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)
    accepted.activity.cancellables.append(
        Cancellable(id="gpu", label="gpu_inference", contract=Cancellation.NON_CANCELLABLE)
    )

    result = await arbiter.interrupt("user_speech")

    assert result.final_state is ActivityState.ABANDONED
    assert result.abandoned == ("gpu_inference",)


async def test_cooperative_child_that_never_stops_is_abandoned(
    arbiter: AttentionArbiter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**待ち続けて barge-in を壊すより、切り離す。** 契約違反なので警告は出す。"""
    monkeypatch.setattr(arbiter_module, "COOPERATIVE_GRACE_S", 0.01)
    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)
    accepted.activity.cancellables.append(
        Cancellable(id="stuck", label="stuck_loop", contract=Cancellation.COOPERATIVE)
    )

    result = await arbiter.interrupt("user_speech")

    assert result.final_state is ActivityState.ABANDONED
    assert result.abandoned == ("stuck_loop",)


async def test_hard_child_that_fails_to_die_is_abandoned(arbiter: AttentionArbiter) -> None:
    """**止まったことにしない。**"""

    async def kill() -> None:
        raise RuntimeError("kill failed")

    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)
    accepted.activity.cancellables.append(
        Cancellable(id="p", label="subprocess", contract=Cancellation.HARD, kill=kill)
    )

    result = await arbiter.interrupt("user_speech")

    assert result.final_state is ActivityState.ABANDONED
    assert result.abandoned == ("subprocess",)


async def test_interrupt_returns_to_idle(arbiter: AttentionArbiter) -> None:
    await arbiter.propose(user_speech())
    await arbiter.interrupt("user_speech")
    assert arbiter.current().kind is ActivityKind.IDLE
    assert arbiter.current().state is ActivityState.RUNNING


# ── Job と inference_lease ─────────────────────────────────


async def test_job_does_not_take_the_foreground(arbiter: AttentionArbiter) -> None:
    """**Job は foreground を取らない**（ADR-018）。lease を取っても current は idle のまま。"""
    job = Job(id=new_job_id(), kind=JobKind.REFLECTION, cancellation=Cancellation.COOPERATIVE)
    async with arbiter.inference_lease(job):
        assert arbiter.current().kind is ActivityKind.IDLE


def test_job_actor_is_always_system() -> None:
    """`system` 固定 → **L0 のツールしか使えない。**"""
    job = Job(id=new_job_id(), kind=JobKind.REFLECTION, cancellation=Cancellation.COOPERATIVE)
    assert job.actor is Actor.SYSTEM


async def test_foreground_inference_revokes_the_job_lease(arbiter: AttentionArbiter) -> None:
    """**これが無いと Reflection が会話の初トークンを待たせ、SLO を直撃する。**"""
    job = Job(
        id=new_job_id(),
        kind=JobKind.REFLECTION,
        cancellation=Cancellation.COOPERATIVE,
        uses_inference=True,
    )
    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)

    async with arbiter.inference_lease(job) as lease:
        before = lease.token.is_set
        arbiter.request_inference(accepted.activity.id)
        assert (before, lease.token.is_set) == (False, True)
        assert lease.token.reason == "inference_revoked"


async def test_background_activity_cannot_revoke_a_lease(arbiter: AttentionArbiter) -> None:
    """foreground でない Activity からの要求は無視する。"""
    job = Job(
        id=new_job_id(),
        kind=JobKind.REFLECTION,
        cancellation=Cancellation.COOPERATIVE,
        uses_inference=True,
    )
    first = await arbiter.propose(user_speech())
    assert isinstance(first, Accepted)
    await arbiter.propose(user_speech())  # first は background に落ちる
    await settle(arbiter)

    async with arbiter.inference_lease(job) as lease:
        arbiter.request_inference(first.activity.id)
        assert not lease.token.is_set


async def test_lease_is_released_on_exit(arbiter: AttentionArbiter) -> None:
    job = Job(
        id=new_job_id(),
        kind=JobKind.REFLECTION,
        cancellation=Cancellation.COOPERATIVE,
        uses_inference=True,
    )
    accepted = await arbiter.propose(user_speech())
    assert isinstance(accepted, Accepted)

    async with arbiter.inference_lease(job):
        pass

    # 抜けた後の要求は、もう誰の lease も revoke しない
    arbiter.request_inference(accepted.activity.id)
    assert not job.cancel_token.is_set
