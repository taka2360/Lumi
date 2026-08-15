"""Phase 0 の「こんにちは」。

roadmap Phase 0「ハードコードされた『こんにちは』を AivisSpeech で発話 → リップシンク」

**ここに賢さは無い。** 何を言うかは固定文字列であり、いつ言うかは
「Stage が繋がってエンジンが起動したら1回」である。
Phase 1 で `agent/reactive` に置き換わり、このファイルは消える。

貫通させたいのは中身ではなく経路:

```
セットアップ状態 → エンジン起動 → 合成（音声 + 口のタイムライン）
  → stage.speech.started → 再生 → stage.speech.ended
```
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from lumi import logging as lumi_logging
from lumi.audio.playback import PlaybackError, play_wav
from lumi.providers.tts.aivisspeech import AivisSpeechClient, TtsError
from lumi.providers.tts.engine_process import EngineProcess
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.state import EngineRuntime
from lumi.transport.protocol import Role
from lumi.transport.server import WsServer

log = lumi_logging.get_logger(__name__)

#: Phase 0 で喋る唯一の文。**Lumi 自身の文字列なので TRUSTED**（外部由来ではない）。
GREETING = "こんにちは"

#: 発話の開始と終了（Core → Stage）。契約 → docs/interfaces/renderer.md
METHOD_SPEECH_STARTED = "stage.speech.started"
METHOD_SPEECH_ENDED = "stage.speech.ended"


class Greeter:
    """一度だけ挨拶する。エンジンプロセスの持ち主でもある。"""

    def __init__(self, server: WsServer, setup: SetupCoordinator) -> None:
        self._server = server
        self._setup = setup
        self._engine: EngineProcess | None = None
        self._done = False

    async def greet_once(self) -> None:
        """喋れる状態なら1回だけ喋る。**喋れないなら何もしない**（状態は既に配信済み）。"""
        if self._done:
            return
        self._done = True

        state = self._setup.state
        if not state.usable or state.port is None:
            # 未セットアップ / 取得失敗。**壊れているのではない。**
            # SetupPanel が既に表示しているので、ここで追加の通知はしない。
            log.info("greeting.skipped", state=str(state.state))
            return

        runtime = await self._start_engine(state.port, state.executable)
        if runtime is not EngineRuntime.READY:
            return

        await self._speak(state.port)

    async def _start_engine(self, port: int, executable: str | None) -> EngineRuntime:
        self._engine = EngineProcess(Path(executable) if executable else None, port)
        await self._setup.set_runtime(EngineRuntime.STARTING)
        runtime = await self._engine.ensure_running()
        await self._setup.set_runtime(runtime)
        return runtime

    async def _speak(self, port: int) -> None:
        client = AivisSpeechClient(port)
        try:
            speaker = await client.default_speaker()
            if speaker is None:
                raise TtsError("no_speaker", "エンジンに音声モデルが登録されていません")
            audio = await client.synthesize(GREETING, speaker)
        except TtsError as error:
            # **喋れないことを「壊れている」として残す。** 黙って無音にしない。
            log.warning("greeting.failed", reason=error.reason, detail=error.detail)
            await self._setup.set_runtime(EngineRuntime.FAILED)
            return

        # **再生開始と同時に口を動かし始める。** 時刻は Stage 側の時計で進む
        # （60Hz で送ると Stage が詰まったときに口が固まる）。
        payload: dict[str, object] = {"text": GREETING}
        if audio.timeline is not None:
            payload.update(audio.timeline.to_payload())
        # **タイムラインが無ければビセームを送らない**（口は閉じたまま）。
        # でたらめな時間で動かすより動かさない → docs/interfaces/renderer.md
        await self._server.notify(Role.STAGE, METHOD_SPEECH_STARTED, payload)
        try:
            await play_wav(audio.wav)
        except PlaybackError as error:
            log.warning("greeting.playback_failed", reason=error.reason, detail=error.detail)
        finally:
            # **必ず閉じる。** ここを通らないと口が開きっぱなしになる。
            await self._server.notify(Role.STAGE, METHOD_SPEECH_ENDED, {})
        log.info("greeting.spoken", text=GREETING)

    async def aclose(self) -> None:
        """**Lumi が起動したエンジンだけ止める**（docs/architecture/core.md §6）。"""
        if self._engine is None:
            return
        try:
            async with asyncio.timeout(30.0):
                await self._engine.stop()
        except TimeoutError:
            log.warning("tts.engine.stop_timeout")
