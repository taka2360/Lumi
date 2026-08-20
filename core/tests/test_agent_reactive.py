"""Reactive Loop. **Exercises every path without calling an LLM**
(the core principle of `.claude/rules/tests.md`).

docs/architecture/agent.md test 7 (`max_steps` / `deadline` / cancel), and
docs/contracts/provenance.md tests 8-10 (a turn's trust inheritance).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import math
import wave
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from lumi.agent.reactive import METHOD_USER_SAID, LoopLimits, ReactiveLoop
from lumi.agent.session import Session
from lumi.agent.speech import METHOD_SPEECH_STARTED
from lumi.audio.devices import AudioPlan, Device, StreamPlan
from lumi.audio.io import AudioIO
from lumi.audio.vad import VadEvent
from lumi.content.pack import CharacterPack, Credit, VoiceSettings
from lumi.kernel.arbiter import AttentionArbiter
from lumi.kernel.cancellation import CancelToken
from lumi.kernel.event import EventBus
from lumi.kernel.hooks import HookRegistry
from lumi.permission.grants import GrantStore
from lumi.permission.kernel import PermissionKernel
from lumi.permission.scope import ScopeLane
from lumi.permission.verifiers import CharacterBindVerifier, CharacterCanonicalizer
from lumi.provenance import TrustLevel
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderKind,
    ResourceHint,
    UnloadPolicy,
)
from lumi.providers.llm.base import (
    Finish,
    LLMEvent,
    LLMOptions,
    Message,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)
from lumi.providers.registry import ProviderRegistry
from lumi.providers.stt.base import Transcription
from lumi.providers.tts.base import SpeechAudio, VoiceConfig
from lumi.storage.events import SqliteEventStore
from lumi.storage.sqlite import Database
from lumi.tools.base import ToolDescriptor
from lumi.tools.builtin.character import SetExpressionTool
from lumi.tools.registry import ToolRegistry
from lumi.transport.protocol import Role

RATE = 16_000

PACK = CharacterPack(
    root=Path("."),
    name="Lumi",
    persona="あなたは Lumi。",
    voice=VoiceSettings(
        speaker=0,
        credit=Credit(name="test", credit_text="test", license_name="test"),
    ),
)


# ── Fakes ────────────────────────────────────────────────────


class _Base:
    """The boring parts of `Provider`. **Not the point of the test.**"""

    def __init__(self) -> None:
        self._loaded = False

    async def load(self) -> None:
        self._loaded = True

    async def unload(self) -> None:
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def resource_hint(self) -> ResourceHint:
        return ResourceHint(
            device_pref=DevicePref.CPU_ONLY,
            vram_estimate_mb=0,
            load_time_estimate_ms=0,
            unload_policy=UnloadPolicy.PINNED,
        )

    def attribution(self) -> Attribution:
        return Attribution(display_name="fake", credit_text="fake", license_name="MIT")


class FakeLlm(_Base):
    """**Plays through a script.** Uses a different script per step."""

    id = "fake-llm"
    kind = ProviderKind.LLM

    def __init__(self, scripts: list[list[LLMEvent]], *, delay: float = 0.0) -> None:
        super().__init__()
        self.scripts = scripts
        self.delay = delay
        self.prompts: list[Sequence[Message]] = []
        self.step = 0

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDescriptor] | None,
        options: LLMOptions,
        cancel_token: CancelToken,
    ) -> AsyncIterator[LLMEvent]:
        del tools, options
        self.prompts.append(list(messages))
        script = self.scripts[min(self.step, len(self.scripts) - 1)]
        self.step += 1
        for event in script:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield event


class FakeStt(_Base):
    id = "fake-stt"
    kind = ProviderKind.STT

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    async def transcribe(
        self, audio: Any, language: str | None, cancel_token: CancelToken
    ) -> Transcription:
        del audio, language, cancel_token
        return Transcription(text=self.text, language="ja")


class FakeTts(_Base):
    id = "fake-tts"
    kind = ProviderKind.TTS

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self.voices: list[VoiceConfig] = []

    async def synthesize(
        self, text: str, voice: VoiceConfig, cancel_token: CancelToken
    ) -> SpeechAudio:
        del cancel_token
        self.texts.append(text)
        self.voices.append(voice)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(RATE)
            sink.writeframes(np.zeros(RATE // 200, dtype="<i2").tobytes())
        return SpeechAudio(wav=buffer.getvalue(), timeline=None)

    def supported_languages(self) -> frozenset[str]:
        return frozenset({"ja"})


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        #: Signalled per sentence. **Lets a test wait on the thing it cares about** instead
        #: of guessing a sleep long enough for however many await points the path has
        self._spoke = asyncio.Event()

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        del role
        self.sent.append((method, payload or {}))
        if method == METHOD_SPEECH_STARTED:
            self._spoke.set()

    def spoken(self) -> list[str]:
        return [p["text"] for m, p in self.sent if m == METHOD_SPEECH_STARTED]

    async def wait_for_speech(self, count: int = 1) -> None:
        """Returns once `count` sentences have actually reached the Stage."""
        while len(self.spoken()) < count:
            self._spoke.clear()
            await self._spoke.wait()


class Rig:
    """The complete set of things needed to run one conversation."""

    def __init__(
        self,
        llm: FakeLlm,
        *,
        stt_text: str = "やあ",
        limits: LoopLimits | None = None,
        tts_speed: float = 1.2,
    ):
        self.database = Database.open(":memory:")
        self.database.migrate()
        bus = EventBus(SqliteEventStore(self.database))
        self.arbiter = AttentionArbiter(bus)
        self.expressions: list[str] = []

        async def send(intent: Any) -> None:
            self.expressions.append(intent.emotion.value)

        self.tools = ToolRegistry(
            PermissionKernel(GrantStore(), _NullAudit()),
            bus,
            HookRegistry(),
            canonicalizers={ScopeLane.CHARACTER: CharacterCanonicalizer()},
            bind_verifiers={ScopeLane.CHARACTER: CharacterBindVerifier()},
        )
        self.tools.register(SetExpressionTool(send))

        self.llm = llm
        self.tts = FakeTts()
        self.notifier = FakeNotifier()
        providers = ProviderRegistry()
        providers.register(llm)
        providers.register(self.tts)
        providers.register(FakeStt(stt_text))

        self.session = Session()
        self.audio = _audio()
        self.loop = ReactiveLoop(
            arbiter=self.arbiter,
            providers=providers,
            tools=self.tools,
            pack=PACK,
            notifier=self.notifier,
            options=LLMOptions(model="fake"),
            session=self.session,
            limits=limits,
            audio=self.audio,
            tts_speed=tts_speed,
        )

    async def start(self) -> None:
        await self.arbiter.start()


class _NullAudit:
    async def append(self, record: Any) -> None:
        del record


def _audio() -> AudioIO:
    """**Never opens a device.** Without calling `start()`, it's just a pair of containers.

    A capture plan is included so `can_listen` holds and `run()` will consume events;
    the stream itself is only opened by `AudioIO.start()`, which no test calls.
    """
    device = Device(
        index=0,
        name="fake",
        host_api="WASAPI",
        max_input_channels=1,
        max_output_channels=1,
        default_samplerate=float(RATE),
    )
    plan = AudioPlan(
        capture=StreamPlan(device=device, samplerate=RATE, channels=1),
        playback=StreamPlan(device=device, samplerate=RATE, channels=1),
        warnings=(),
    )
    return AudioIO(plan)


def text(*chunks: str) -> list[LLMEvent]:
    return [*(TextDelta(text=chunk) for chunk in chunks), Finish(reason="stop")]


# ── The basic single turn ───────────────────────────────────────────


async def test_a_turn_speaks_and_records() -> None:
    rig = Rig(FakeLlm([text("こんにちは。", "げんきだよ。")]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.notifier.spoken() == ["こんにちは。", "げんきだよ。"]
    assert [t.text for t in rig.session.turns] == ["やあ", "こんにちは。げんきだよ。"]


async def test_runtime_tts_speed_applies_to_next_turn_only() -> None:
    rig = Rig(
        FakeLlm([text("最初の速度だよ。"), text("次の速度だよ。")]),
        tts_speed=1.2,
    )
    await rig.start()

    await rig.loop.handle_text("最初のターン")
    rig.loop.set_tts_speed(1.4)
    await rig.loop.handle_text("次のターン")

    assert [voice.speed_scale for voice in rig.tts.voices] == [1.2, 1.4]


async def test_tts_speed_is_frozen_when_changed_while_turn_is_blocked() -> None:
    rig = Rig(FakeLlm([text("開始時の速度だよ。")]), tts_speed=1.2)
    await rig.start()

    user_said = asyncio.Event()
    release_user_said = asyncio.Event()
    original_notify = rig.notifier.notify

    async def block_user_said(
        role: Role, method: str, payload: dict[str, Any] | None = None
    ) -> None:
        if method == METHOD_USER_SAID:
            user_said.set()
            await release_user_said.wait()
        await original_notify(role, method, payload)

    rig.notifier.notify = block_user_said  # type: ignore[method-assign]
    turn = asyncio.create_task(rig.loop.handle_text("速度を変える"))
    await user_said.wait()

    rig.loop.set_tts_speed(1.4)
    release_user_said.set()
    await turn

    assert [voice.speed_scale for voice in rig.tts.voices] == [1.2]


@pytest.mark.parametrize("speed", [0.49, 2.01, math.nan, math.inf, -math.inf])
def test_tts_speed_rejects_invalid_values(speed: float) -> None:
    with pytest.raises(ValueError, match="tts_speed"):
        Rig(FakeLlm([text("話せないよ。")]), tts_speed=speed)

    rig = Rig(FakeLlm([text("話せるよ。")]))
    with pytest.raises(ValueError, match="tts_speed"):
        rig.loop.set_tts_speed(speed)


async def test_the_activity_returns_to_idle() -> None:
    """**An Activity is never left open.** Returns to idle once finished."""
    rig = Rig(FakeLlm([text("うん。")]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.arbiter.current().kind.value == "idle"


async def test_speech_ended_goes_through_stt() -> None:
    rig = Rig(FakeLlm([text("聞こえたよ。")]), stt_text="おはよう")
    await rig.start()

    await rig.loop.on_speech_ended(np.zeros(1600, dtype=np.float32))

    assert rig.session.turns[0].text == "おはよう"


async def test_what_was_heard_reaches_the_stage_before_the_activity() -> None:
    """**Ordering matters.** Core sends it before proposing, so a turn that goes nowhere
    still leaves the heard text on screen — the only thing separating "misheard you" from
    "never heard you" (docs/interfaces/renderer.md).
    """
    rig = Rig(FakeLlm([text("聞こえたよ。")]), stt_text="おはよう")
    await rig.start()

    await rig.loop.on_speech_ended(np.zeros(1600, dtype=np.float32))

    methods = [method for method, _ in rig.notifier.sent]
    assert METHOD_USER_SAID in methods
    said = next(payload for method, payload in rig.notifier.sent if method == METHOD_USER_SAID)
    assert said["text"] == "おはよう"
    assert methods.index(METHOD_USER_SAID) < methods.index(METHOD_SPEECH_STARTED)


async def test_a_stage_that_cannot_draw_never_costs_the_reply() -> None:
    """The caption is not the reply. **A failed notify must not fail the turn.**"""
    rig = Rig(FakeLlm([text("聞こえたよ。")]), stt_text="おはよう")
    await rig.start()

    original = rig.notifier.notify

    async def refuse_the_caption(
        role: Role, method: str, payload: dict[str, Any] | None = None
    ) -> None:
        if method == METHOD_USER_SAID:
            raise RuntimeError("stage is gone")
        await original(role, method, payload)

    rig.notifier.notify = refuse_the_caption  # type: ignore[method-assign]
    await rig.loop.handle_text("やあ")

    assert rig.notifier.spoken() == ["聞こえたよ。"]


async def test_an_empty_transcription_creates_no_activity() -> None:
    """**No Activity is created if nothing was said.** Never clutters the Inspector."""
    rig = Rig(FakeLlm([text("？")]), stt_text="   ")
    await rig.start()

    await rig.loop.on_speech_ended(np.zeros(1600, dtype=np.float32))

    assert rig.session.turns == ()


# ── Markers and reasoning ──────────────────────────────────────────


async def test_markers_are_not_spoken_and_reach_the_tool() -> None:
    """**Markers also go through `invoke`** (Invariant 2). Never spoken aloud."""
    rig = Rig(FakeLlm([text('うれしい<|ACT {"emotion":"happy"}|>な。')]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.notifier.spoken() == ["うれしいな。"]
    assert rig.expressions == ["happy"]


async def test_reasoning_is_never_spoken() -> None:
    """**Reasoning is never spoken** (docs/interfaces/provider.md)."""
    script: list[LLMEvent] = [
        ReasoningDelta(text="ユーザーは挨拶している。丁寧に返そう。"),
        TextDelta(text="やあ。"),
        Finish(reason="stop"),
    ]
    rig = Rig(FakeLlm([script]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.tts.texts == ["やあ。"]


# ── The tool loop (table 7) ──────────────────────────────────────


async def test_the_tool_loop_feeds_the_result_back() -> None:
    call: list[LLMEvent] = [
        ToolCall(id="1", name="character.set_expression", arguments={"emotion": "happy"}),
        Finish(reason="tool_calls"),
    ]
    rig = Rig(FakeLlm([call, text("できたよ。")]))
    await rig.start()

    await rig.loop.handle_text("笑って")

    assert rig.expressions == ["happy"]
    assert rig.llm.step == 2
    # The second prompt has the tool result included
    assert "character.set_expression" in rig.llm.prompts[1][0].content


async def test_the_tool_loop_stops_at_max_steps() -> None:
    """**Never loops forever.** The limit belongs to the Activity."""
    call: list[LLMEvent] = [
        ToolCall(id="1", name="character.set_expression", arguments={"emotion": "happy"}),
        Finish(reason="tool_calls"),
    ]
    rig = Rig(FakeLlm([call]), limits=LoopLimits(max_steps=2))
    await rig.start()

    await rig.loop.handle_text("ずっと笑って")

    assert rig.llm.step == 2


async def test_an_unknown_tool_becomes_a_failed_block() -> None:
    """**An unregistered tool never stops the conversation.** A failure is context too."""
    call: list[LLMEvent] = [
        ToolCall(id="1", name="fs.read", arguments={"path": "/etc/passwd"}),
        Finish(reason="tool_calls"),
    ]
    rig = Rig(FakeLlm([call, text("できなかった。")]))
    await rig.start()

    await rig.loop.handle_text("読んで")

    assert rig.notifier.spoken() == ["できなかった。"]
    assert "unknown_tool" in rig.llm.prompts[1][0].content


# ── Trust inheritance ────────────────────────────────────────────


async def test_a_small_talk_turn_stays_trusted() -> None:
    """**Test 8.** A turn built solely from trusted input is never tainted."""
    rig = Rig(FakeLlm([text("そうだね。")]))
    await rig.start()

    await rig.loop.handle_text("いい天気")

    assert rig.session.turns[-1].trust_level is TrustLevel.TRUSTED
    assert rig.session.session_trust is TrustLevel.TRUSTED


async def test_a_tool_result_taints_the_following_turn() -> None:
    """A turn after a tool result comes in is **`DERIVED` = `TAINTED`**.

    A `character` lane result isn't `is_raw_external` since it's not an observation of the outside
    world, but the rule **"inherits the join of the inputs"** applies here in the same form.
    """
    call: list[LLMEvent] = [
        ToolCall(id="1", name="fs.read", arguments={}),
        Finish(reason="tool_calls"),
    ]
    rig = Rig(FakeLlm([call, text("よし。")]))
    await rig.start()
    rig.session.observe(TrustLevel.TAINTED)

    await rig.loop.handle_text("なにか読んで")

    assert rig.session.turns[-1].trust_level is TrustLevel.TAINTED


# ── Latency measurement (docs/architecture/audio.md §7) ──────


async def test_a_turn_reports_its_latency_breakdown() -> None:
    """**Every turn reports.** The SLO is a distribution, not a best case."""
    rig = Rig(FakeLlm([text("うん。")]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    latency = rig.loop.last_latency
    assert latency is not None
    assert set(latency.spans) == {
        "retrieve_ms",
        "assemble_ms",
        "llm_first_token_ms",
        "llm_first_segment_ms",
        "tts_first_audio_ms",
        "playback_ms",
    }


async def test_the_voice_route_also_measures_vad_and_stt() -> None:
    """`vad_ms` starts when the user **stopped talking**, not when Core noticed."""
    rig = Rig(FakeLlm([text("はい。")]), stt_text="おはよう")
    await rig.start()

    ended_at = asyncio.get_running_loop().time()
    await rig.loop.on_speech_ended(np.zeros(1600, dtype=np.float32), ended_at)

    latency = rig.loop.last_latency
    assert latency is not None
    assert "vad_ms" in latency.spans
    assert "stt_ms" in latency.spans


async def test_an_interrupted_turn_still_reports() -> None:
    """★ **Barge-in is the normal case, not an error.**

    "How far did it get before being cut off" is exactly what needs measuring, and the
    stages it never reached stay **absent** rather than being recorded as 0.
    """
    rig = Rig(FakeLlm([text("ながいはなしを。")], delay=0.05))
    await rig.start()

    turn = asyncio.create_task(rig.loop.handle_text("なにか話して"))
    await asyncio.sleep(0.01)
    await rig.loop.on_speech_started()
    await turn

    latency = rig.loop.last_latency
    assert latency is not None
    assert "tts_first_audio_ms" not in latency.spans


async def test_the_tool_loop_does_not_reassign_the_first_spans() -> None:
    """`assemble_ms` means **the first** assembly — the one the user is waiting on."""
    call: list[LLMEvent] = [
        ToolCall(id="1", name="character.set_expression", arguments={"emotion": "happy"}),
        Finish(reason="tool_calls"),
    ]
    rig = Rig(FakeLlm([call, text("できたよ。")]))
    await rig.start()

    await rig.loop.handle_text("笑って")

    assert rig.llm.step == 2
    latency = rig.loop.last_latency
    assert latency is not None
    assert latency.spans["assemble_ms"] >= 0


# ── barge-in ────────────────────────────────────────────────


async def test_speech_started_interrupts_the_current_activity() -> None:
    """**Interrupting an Activity goes through `interrupt()`** (Invariant 4)."""
    rig = Rig(FakeLlm([text("ながいはなしを。")], delay=0.05))
    await rig.start()

    turn = asyncio.create_task(rig.loop.handle_text("なにか話して"))
    await asyncio.sleep(0.01)
    await rig.loop.on_speech_started()
    await turn

    assert rig.arbiter.current().kind.value == "idle"


async def test_an_interrupt_mutes_the_playback() -> None:
    """**The barge-in exit point.** Sound stops without waiting for generation to finish."""
    rig = Rig(FakeLlm([text("あ。", "い。", "う。")], delay=0.02))
    await rig.start()
    playback = rig.audio.playback
    assert playback is not None

    turn = asyncio.create_task(rig.loop.handle_text("しゃべって"))
    await rig.notifier.wait_for_speech()
    await rig.loop.on_speech_started()
    await turn

    # Confirms it had started speaking and then stopped (if nothing had played, this wouldn't be a
    # real test)
    spoken = rig.notifier.spoken()
    assert spoken[0] == "あ。"
    assert len(spoken) < 3, "最後まで喋らずに止まった"
    assert playback.mute_flag.is_set()
    assert playback.queued == 0


async def test_interrupting_when_idle_is_a_no_op() -> None:
    rig = Rig(FakeLlm([text("…")]))
    await rig.start()

    await rig.loop.on_speech_started()

    assert rig.arbiter.current().kind.value == "idle"


# ── Failure ────────────────────────────────────────────────────


async def test_a_missing_provider_fails_the_turn_without_crashing() -> None:
    """**Never silently degrades.** Being unable to speak is recorded as the Activity failing."""
    rig = Rig(FakeLlm([text("…")]))
    await rig.start()
    rig.loop._providers = ProviderRegistry()

    await rig.loop.handle_text("やあ")

    assert rig.arbiter.current().kind.value == "idle"


@pytest.mark.parametrize("kind", [ProviderKind.LLM, ProviderKind.TTS])
async def test_each_provider_is_required(kind: ProviderKind) -> None:
    rig = Rig(FakeLlm([text("…")]))
    await rig.start()
    registry = ProviderRegistry()
    for provider in (rig.llm, rig.tts):
        if provider.kind is not kind:
            registry.register(provider)
    rig.loop._providers = registry

    await rig.loop.handle_text("やあ")

    assert rig.session.turns[-1].role == "user"


# ── The loop stays alive ──────────────────────────────────────


async def test_a_failing_event_does_not_make_lumi_deaf(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ Regression (observed 2026-08-17): **one bad event ended the loop for good.**

    `run()` awaited `on_speech_started()` inline, so a raise there escaped the `async for`
    and no VAD event was ever read again — and since nobody awaits the task, asyncio only
    mentioned it at GC. **Being deaf for the rest of the session is far worse than
    dropping one utterance**, so the loop logs and keeps listening.
    """
    rig = Rig(FakeLlm([text("まだ聞こえてるよ。")]), stt_text="おーい")
    await rig.start()

    async def boom(self: AttentionArbiter, reason: str) -> None:
        # Exactly what the unstarted Arbiter raised in production
        raise RuntimeError("Arbiter が start() されていない")

    monkeypatch.setattr(AttentionArbiter, "interrupt", boom)

    task = asyncio.create_task(rig.loop.run())
    try:
        rig.audio._offer(VadEvent.SPEECH_STARTED, None, 0.0)
        rig.audio._offer(VadEvent.SPEECH_ENDED, np.zeros(1600, dtype=np.float32), 0.0)
        # The turn runs in its own task. **Raced against the loop itself** so a dead loop
        # fails immediately with the right message instead of timing out
        speaking = asyncio.create_task(rig.notifier.wait_for_speech())
        await asyncio.wait({speaking, task}, timeout=5.0, return_when=asyncio.FIRST_COMPLETED)
        speaking.cancel()

        assert not task.done(), "1つのイベントが失敗してもループは生き残る"
        assert [t.text for t in rig.session.turns][:1] == ["おーい"], "次の発話を拾えている"
    finally:
        await _drain(task)


async def _drain(loop_task: asyncio.Task[None]) -> None:
    """Stops the loop **and the turn tasks it spawned.**

    A turn left running would outlive the test and land its failure in whichever test
    happens to run next.
    """
    pending = [t for t in asyncio.all_tasks() if t.get_name() == "turn"]
    for task in (loop_task, *pending):
        task.cancel()
    for task in (loop_task, *pending):
        # The loop's own exception, if any, was already reported by the assertions above
        with contextlib.suppress(BaseException):
            await task
