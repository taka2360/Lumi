"""Phase 0 の発話経路。**エンジンも音も鳴らさない**（差し替える）。

確かめるのは経路と失敗の扱い:
「喋れないなら黙って何もしない」「口を必ず閉じる」「起動できないなら failed にする」
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from lumi import greeting as greeting_module
from lumi.greeting import METHOD_SPEECH_ENDED, METHOD_SPEECH_STARTED, Greeter
from lumi.providers.tts.aivisspeech import TtsError
from lumi.providers.tts.base import SpeechAudio
from lumi.providers.tts.viseme import Viseme, VisemeSpan, VisemeTimeline
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.state import EngineRuntime, TtsSetup, TtsSetupState
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer

WAV = b"RIFF-not-really-a-wav"
TIMELINE = VisemeTimeline(spans=(VisemeSpan(Viseme.A, 0, 200),), total_ms=200)


class FakeServer:
    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []

    async def notify(self, role: Role, method: str, payload: dict[str, Any] | None = None) -> None:
        assert role is Role.STAGE
        self.notifications.append({"method": method, **(payload or {})})

    def methods(self) -> list[str]:
        return [item["method"] for item in self.notifications]

    def as_server(self) -> WsServer:
        return cast(WsServer, self)


class FakeCoordinator:
    """`SetupCoordinator` のうち Greeter が使う2つだけを持つ。"""

    def __init__(self, state: TtsSetup) -> None:
        self.state = state
        self.runtimes: list[EngineRuntime] = []

    async def set_runtime(self, runtime: EngineRuntime) -> None:
        self.runtimes.append(runtime)
        self.state = replace(self.state, runtime=runtime)

    def as_coordinator(self) -> SetupCoordinator:
        return cast(SetupCoordinator, self)


def installed(**overrides: Any) -> TtsSetup:
    base: dict[str, Any] = {
        "state": TtsSetupState.INSTALLED,
        "engine_name": "AivisSpeech",
        "version": "1.2.0",
        "port": 10101,
        "executable": "C:/x/run.exe",
    }
    base.update(overrides)
    return TtsSetup(**base)


class FakeEngine:
    def __init__(self, runtime: EngineRuntime) -> None:
        self._runtime = runtime
        self.stopped = False

    async def ensure_running(self) -> EngineRuntime:
        return self._runtime

    async def stop(self) -> None:
        self.stopped = True


class FakeClient:
    def __init__(self, audio: SpeechAudio | None = None, error: TtsError | None = None) -> None:
        self._audio = audio
        self._error = error

    async def default_speaker(self) -> int | None:
        return 1

    async def synthesize(self, text: str, speaker: int) -> SpeechAudio:
        del text, speaker
        if self._error is not None:
            raise self._error
        assert self._audio is not None
        return self._audio


def wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    runtime: EngineRuntime = EngineRuntime.READY,
    client: FakeClient | None = None,
    played: list[bytes] | None = None,
    playback_error: Exception | None = None,
) -> FakeEngine:
    engine = FakeEngine(runtime)
    monkeypatch.setattr(greeting_module, "EngineProcess", lambda *_args: engine)
    monkeypatch.setattr(
        greeting_module,
        "AivisSpeechClient",
        lambda *_args: client or FakeClient(SpeechAudio(WAV, TIMELINE)),
    )

    async def play(data: bytes) -> None:
        if playback_error is not None:
            raise playback_error
        if played is not None:
            played.append(data)

    monkeypatch.setattr(greeting_module, "play_wav", play)
    return engine


class TestGreeting:
    async def test_speaks_once_and_frames_it_with_start_and_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        played: list[bytes] = []
        wire(monkeypatch, played=played)
        server, setup = FakeServer(), FakeCoordinator(installed())

        greeter = Greeter(server.as_server(), setup.as_coordinator())
        await greeter.greet_once()
        await greeter.greet_once()  # 2回目は何もしない

        assert played == [WAV]
        assert server.methods().count(METHOD_SPEECH_STARTED) == 1
        assert server.methods().count(METHOD_SPEECH_ENDED) == 1
        # **口を動かし始めるのは再生の前。**
        assert server.methods().index(METHOD_SPEECH_STARTED) < server.methods().index(
            METHOD_SPEECH_ENDED
        )

    async def test_sends_the_timeline_with_the_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wire(monkeypatch)
        server, setup = FakeServer(), FakeCoordinator(installed())
        await Greeter(server.as_server(), setup.as_coordinator()).greet_once()

        started = next(n for n in server.notifications if n["method"] == METHOD_SPEECH_STARTED)
        assert started["text"] == greeting_module.GREETING
        assert started["total_ms"] == 200
        assert started["spans"][0]["viseme"] == "A"

    async def test_reports_the_runtime_state_while_starting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wire(monkeypatch)
        setup = FakeCoordinator(installed())
        await Greeter(FakeServer().as_server(), setup.as_coordinator()).greet_once()
        assert setup.runtimes == [EngineRuntime.STARTING, EngineRuntime.READY]

    async def test_stays_silent_when_tts_is_not_set_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wire(monkeypatch)
        server = FakeServer()
        setup = FakeCoordinator(TtsSetup(state=TtsSetupState.NOT_CONFIGURED))

        await Greeter(server.as_server(), setup.as_coordinator()).greet_once()

        # **壊れているのではない。** 追加の通知も、エンジンの起動もしない。
        assert server.notifications == []
        assert setup.runtimes == []

    async def test_does_not_speak_when_the_engine_cannot_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wire(monkeypatch, runtime=EngineRuntime.FAILED)
        server, setup = FakeServer(), FakeCoordinator(installed())

        await Greeter(server.as_server(), setup.as_coordinator()).greet_once()

        assert server.notifications == []
        # 「入っているのに起動できない」= 壊れている、として残す。
        assert setup.runtimes == [EngineRuntime.STARTING, EngineRuntime.FAILED]

    async def test_marks_failure_when_synthesis_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wire(monkeypatch, client=FakeClient(error=TtsError("no_moras")))
        server, setup = FakeServer(), FakeCoordinator(installed())

        await Greeter(server.as_server(), setup.as_coordinator()).greet_once()

        assert METHOD_SPEECH_STARTED not in server.methods()
        assert setup.runtimes[-1] is EngineRuntime.FAILED

    async def test_closes_the_mouth_even_when_playback_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lumi.audio.playback import PlaybackError

        wire(monkeypatch, playback_error=PlaybackError("audio_backend_unavailable"))
        server, setup = FakeServer(), FakeCoordinator(installed())

        await Greeter(server.as_server(), setup.as_coordinator()).greet_once()

        # **口が開きっぱなしにならない。**
        assert server.methods() == [METHOD_SPEECH_STARTED, METHOD_SPEECH_ENDED]

    async def test_stops_only_the_engine_it_started(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = wire(monkeypatch)
        greeter = Greeter(FakeServer().as_server(), FakeCoordinator(installed()).as_coordinator())

        await greeter.aclose()
        assert not engine.stopped, "起動していないのに止めようとしてはいけない"

        await greeter.greet_once()
        await greeter.aclose()
        assert engine.stopped
