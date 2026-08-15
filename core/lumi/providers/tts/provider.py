"""`AivisSpeechProvider`。既存の HTTP クライアントを `TTSProvider` に適合させる。

設計 → docs/interfaces/provider.md / 所有と生存 → docs/architecture/core.md §6

## エンジンプロセスの所有

**Lumi が起動したものだけ Lumi が止める。** すでに動いているエンジンには触らない
（ユーザーが自分で起動したものかもしれない）。この規則は `EngineProcess` が実装しており、
Provider はそれを `load()` / `unload()` に対応づけるだけ。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from lumi import logging as lumi_logging
from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderFailed,
    ProviderKind,
    ProviderUnavailable,
    ResourceHint,
    UnloadPolicy,
)
from lumi.providers.tts.aivisspeech import AivisSpeechClient, TtsError
from lumi.providers.tts.base import SpeechAudio, VoiceConfig
from lumi.providers.tts.engine_process import EngineProcess
from lumi.setup.state import EngineRuntime

log = lumi_logging.get_logger(__name__)

#: エンジンが応答するまでの猶予。**無限に待たない**（起動しないことと区別できなくなる）
START_TIMEOUT_S: Final = 180.0


class AivisSpeechProvider:
    """`TTSProvider` の実装。**別プロセス・CPU なので VRAM を消費しない。**"""

    id = "aivisspeech"
    kind = ProviderKind.TTS

    __slots__ = ("_client", "_default_speaker", "_engine", "_loaded", "_speaker_name")

    def __init__(self, port: int, *, executable: Path | None = None) -> None:
        self._client = AivisSpeechClient(port)
        self._engine = EngineProcess(executable, port)
        self._default_speaker: int | None = None
        self._speaker_name = ""
        self._loaded = False

    async def load(self) -> None:
        """**冪等。** エンジンを起動（既に動いていれば触らない）し、既定の話者を確定させる。"""
        if self._loaded:
            return

        runtime = await self._engine.ensure_running()
        if runtime is not EngineRuntime.READY:
            raise ProviderUnavailable("engine_not_ready", f"エンジンの状態: {runtime}")

        try:
            speaker = await self._client.default_speaker()
        except TtsError as error:
            raise ProviderUnavailable(error.reason, error.detail) from error

        if speaker is None:
            # 入っているのに喋れない = **壊れている**（未セットアップとは違う）
            raise ProviderUnavailable("no_speaker", "エンジンに音声モデルが登録されていません")

        self._default_speaker = speaker
        self._loaded = True
        log.info("tts.loaded", provider=self.id, speaker=speaker)

    async def unload(self) -> None:
        """**Lumi が起動したエンジンだけ止める**（`EngineProcess` が判定する）。"""
        self._loaded = False
        await self._engine.stop()

    def is_loaded(self) -> bool:
        return self._loaded

    def default_voice(self) -> VoiceConfig:
        """Content Pack が無い間の既定。**Phase 1 後半で `voice.toml` に移す。**"""
        if self._default_speaker is None:
            raise ProviderUnavailable("not_loaded", "load() の前に声を決められない")
        return VoiceConfig(speaker=self._default_speaker, name=self._speaker_name)

    async def synthesize(
        self, text: str, voice: VoiceConfig, cancel_token: CancelToken
    ) -> SpeechAudio:
        """**合成そのものは `non_cancellable`。**

        `cancel_token` は「まだ意味があるか」を**始める前に**見るために使う。
        1文の合成は数百 ms で終わるので、途中で捨てるより
        「生成したが再生しない」（呼び出し側が破棄する）方が単純で速い。
        """
        if cancel_token.is_set:
            raise ProviderFailed("cancelled", cancel_token.reason or "")
        try:
            return await self._client.synthesize(text, voice.speaker)
        except TtsError as error:
            raise ProviderFailed(error.reason, error.detail) from error

    def supported_languages(self) -> frozenset[str]:
        return frozenset({"ja"})

    def resource_hint(self) -> ResourceHint:
        """**VRAM 0。** これが TTS 選定の主因（ADR-008 / DESIGN.md §7）。"""
        return ResourceHint(
            device_pref=DevicePref.EXTERNAL_PROCESS,
            vram_estimate_mb=0,
            load_time_estimate_ms=int(START_TIMEOUT_S * 1000),
            unload_policy=UnloadPolicy.PINNED,
        )

    def attribution(self) -> Attribution:
        """エンジンのクレジット。

        **保証しないこと**: ここが返すのはエンジンの情報であって、
        **使用中の音声モデルのライセンス全文ではない。** 全文はモデル自身の manifest に入っており
        （docs/licensing.md §4.4）、その読み取りは Phase 2 以降で足す。
        Phase 0 のクレジット画面は既定モデルの ACML 1.0 全文を静的に載せている。
        """
        return Attribution(
            display_name="AivisSpeech",
            credit_text="TTS: AivisSpeech Engine",
            license_name="LGPL-3.0",
            license_url="https://github.com/Aivis-Project/AivisSpeech-Engine/blob/master/LICENSE",
            homepage_url="https://aivis-project.com",
        )
