"""Reactive Loop。**LLM を呼ばずに全経路を通す**（`.claude/rules/tests.md` の大原則）。

docs/architecture/agent.md テスト 7（`max_steps` / `deadline` / cancel）と、
docs/contracts/provenance.md テスト 8〜10（ターンの trust の継承）。
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from lumi.agent.reactive import LoopLimits, ReactiveLoop
from lumi.agent.session import Session
from lumi.agent.speech import METHOD_SPEECH_STARTED
from lumi.audio.devices import AudioPlan, Device, StreamPlan
from lumi.audio.io import AudioIO
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


# ── 偽物 ────────────────────────────────────────────────────


class _Base:
    """`Provider` の退屈な部分。**テストの本題ではない。**"""

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
    """**台本どおりに流す。** ステップごとに別の台本を使う。"""

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

    async def synthesize(
        self, text: str, voice: VoiceConfig, cancel_token: CancelToken
    ) -> SpeechAudio:
        del voice, cancel_token
        self.texts.append(text)
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

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        del role
        self.sent.append((method, payload or {}))

    def spoken(self) -> list[str]:
        return [p["text"] for m, p in self.sent if m == METHOD_SPEECH_STARTED]


class Rig:
    """1本の会話を回すのに要るもの一式。"""

    def __init__(self, llm: FakeLlm, *, stt_text: str = "やあ", limits: LoopLimits | None = None):
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
        )

    async def start(self) -> None:
        await self.arbiter.start()


class _NullAudit:
    async def append(self, record: Any) -> None:
        del record


def _audio() -> AudioIO:
    """**デバイスを開かない。** `start()` を呼ばなければ出力の入れ物があるだけ。"""
    device = Device(
        index=0,
        name="fake",
        host_api="WASAPI",
        max_input_channels=0,
        max_output_channels=1,
        default_samplerate=float(RATE),
    )
    plan = AudioPlan(
        capture=None,
        playback=StreamPlan(device=device, samplerate=RATE, channels=1),
        warnings=(),
    )
    return AudioIO(plan)


def text(*chunks: str) -> list[LLMEvent]:
    return [*(TextDelta(text=chunk) for chunk in chunks), Finish(reason="stop")]


# ── 基本の1ターン ───────────────────────────────────────────


async def test_a_turn_speaks_and_records() -> None:
    rig = Rig(FakeLlm([text("こんにちは。", "げんきだよ。")]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.notifier.spoken() == ["こんにちは。", "げんきだよ。"]
    assert [t.text for t in rig.session.turns] == ["やあ", "こんにちは。げんきだよ。"]


async def test_the_activity_returns_to_idle() -> None:
    """**Activity を開きっぱなしにしない。** 終わったら idle に戻る。"""
    rig = Rig(FakeLlm([text("うん。")]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.arbiter.current().kind.value == "idle"


async def test_speech_ended_goes_through_stt() -> None:
    rig = Rig(FakeLlm([text("聞こえたよ。")]), stt_text="おはよう")
    await rig.start()

    await rig.loop.on_speech_ended(np.zeros(1600, dtype=np.float32))

    assert rig.session.turns[0].text == "おはよう"


async def test_an_empty_transcription_creates_no_activity() -> None:
    """**何も言っていないなら Activity を作らない。** Inspector を汚さない。"""
    rig = Rig(FakeLlm([text("？")]), stt_text="   ")
    await rig.start()

    await rig.loop.on_speech_ended(np.zeros(1600, dtype=np.float32))

    assert rig.session.turns == ()


# ── マーカーと思考 ──────────────────────────────────────────


async def test_markers_are_not_spoken_and_reach_the_tool() -> None:
    """**マーカーも `invoke` を通る**（Invariant 2）。読み上げはされない。"""
    rig = Rig(FakeLlm([text('うれしい<|ACT {"emotion":"happy"}|>な。')]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.notifier.spoken() == ["うれしいな。"]
    assert rig.expressions == ["happy"]


async def test_reasoning_is_never_spoken() -> None:
    """**思考は音声化しない**（docs/interfaces/provider.md）。"""
    script: list[LLMEvent] = [
        ReasoningDelta(text="ユーザーは挨拶している。丁寧に返そう。"),
        TextDelta(text="やあ。"),
        Finish(reason="stop"),
    ]
    rig = Rig(FakeLlm([script]))
    await rig.start()

    await rig.loop.handle_text("やあ")

    assert rig.tts.texts == ["やあ。"]


# ── ツールループ（表7）──────────────────────────────────────


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
    # 2回目のプロンプトにツール結果が入っている
    assert "character.set_expression" in rig.llm.prompts[1][0].content


async def test_the_tool_loop_stops_at_max_steps() -> None:
    """**無限に回さない。** 上限は Activity が持つ。"""
    call: list[LLMEvent] = [
        ToolCall(id="1", name="character.set_expression", arguments={"emotion": "happy"}),
        Finish(reason="tool_calls"),
    ]
    rig = Rig(FakeLlm([call]), limits=LoopLimits(max_steps=2))
    await rig.start()

    await rig.loop.handle_text("ずっと笑って")

    assert rig.llm.step == 2


async def test_an_unknown_tool_becomes_a_failed_block() -> None:
    """**登録されていないツールで会話が止まらない。** 失敗も文脈である。"""
    call: list[LLMEvent] = [
        ToolCall(id="1", name="fs.read", arguments={"path": "/etc/passwd"}),
        Finish(reason="tool_calls"),
    ]
    rig = Rig(FakeLlm([call, text("できなかった。")]))
    await rig.start()

    await rig.loop.handle_text("読んで")

    assert rig.notifier.spoken() == ["できなかった。"]
    assert "unknown_tool" in rig.llm.prompts[1][0].content


# ── trust の継承 ────────────────────────────────────────────


async def test_a_small_talk_turn_stays_trusted() -> None:
    """**テスト8。** trusted な入力だけで作ったターンは汚染されない。"""
    rig = Rig(FakeLlm([text("そうだね。")]))
    await rig.start()

    await rig.loop.handle_text("いい天気")

    assert rig.session.turns[-1].trust_level is TrustLevel.TRUSTED
    assert rig.session.session_trust is TrustLevel.TRUSTED


async def test_a_tool_result_taints_the_following_turn() -> None:
    """ツール結果が入った後のターンは **`DERIVED` = `TAINTED`**。

    `character` lane の結果は外界の観測ではないので `is_raw_external` ではないが、
    **入力の join を継承する**という規則はここでも同じ形で働く。
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


# ── barge-in ────────────────────────────────────────────────


async def test_speech_started_interrupts_the_current_activity() -> None:
    """**Activity の中断は `interrupt()` を経由する**（Invariant 4）。"""
    rig = Rig(FakeLlm([text("ながいはなしを。")], delay=0.05))
    await rig.start()

    turn = asyncio.create_task(rig.loop.handle_text("なにか話して"))
    await asyncio.sleep(0.01)
    await rig.loop.on_speech_started()
    await turn

    assert rig.arbiter.current().kind.value == "idle"


async def test_an_interrupt_mutes_the_playback() -> None:
    """**barge-in の出口。** 生成完了を待たずに音が止まる。"""
    rig = Rig(FakeLlm([text("あ。", "い。", "う。")], delay=0.02))
    await rig.start()
    playback = rig.audio.playback
    assert playback is not None

    turn = asyncio.create_task(rig.loop.handle_text("しゃべって"))
    await asyncio.sleep(0.06)
    await rig.loop.on_speech_started()
    await turn

    # 喋り始めていたのに止まった、であること（何も鳴っていなければ試験になっていない）
    assert rig.notifier.spoken() == ["あ。"]
    assert playback.mute_flag.is_set()
    assert playback.queued == 0


async def test_interrupting_when_idle_is_a_no_op() -> None:
    rig = Rig(FakeLlm([text("…")]))
    await rig.start()

    await rig.loop.on_speech_started()

    assert rig.arbiter.current().kind.value == "idle"


# ── 失敗 ────────────────────────────────────────────────────


async def test_a_missing_provider_fails_the_turn_without_crashing() -> None:
    """**黙って劣化しない。** 喋れないことは Activity の失敗として残る。"""
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
