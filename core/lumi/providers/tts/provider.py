"""`AivisSpeechProvider`. Adapts the existing HTTP client to `TTSProvider`.

Design → docs/interfaces/provider.md / Ownership and lifetime → docs/architecture/core.md §6

## Ownership of the engine process

**Lumi only stops what Lumi itself started.** An already-running engine is left alone
(the user might have started it themselves). This rule is implemented by
`EngineProcess`; the Provider just maps it onto `load()` / `unload()`.
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
from lumi.providers.device import DeviceChoice, resolve
from lumi.providers.tts.aivisspeech import AivisSpeechClient, TtsError
from lumi.providers.tts.base import SpeechAudio, VoiceConfig
from lumi.providers.tts.engine_process import EngineProcess
from lumi.setup.state import EngineRuntime

log = lumi_logging.get_logger(__name__)

# : Grace period before the engine responds. **Never wait forever** (couldn't tell it apart from
# never starting)
START_TIMEOUT_S: Final = 180.0


class AivisSpeechProvider:
    """Implementation of `TTSProvider`. **A separate CPU process, so it uses no VRAM.**"""

    id = "aivisspeech"
    kind = ProviderKind.TTS

    __slots__ = ("_client", "_default_speaker", "_device", "_engine", "_loaded", "_speaker_name")

    def __init__(
        self,
        port: int,
        *,
        executable: Path | None = None,
        device: DeviceChoice | str = DeviceChoice.AUTO,
    ) -> None:
        self._client = AivisSpeechClient(port)
        self._engine = EngineProcess(executable, port, device=device)
        self._device = resolve(device)
        self._default_speaker: int | None = None
        self._speaker_name = ""
        self._loaded = False

    async def load(self) -> None:
        """**Idempotent.** Starts the engine (leaves it alone if already running) and settles on a
        default speaker.
        """
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
            # Installed but can't speak = **broken** (different from not-set-up)
            raise ProviderUnavailable("no_speaker", "エンジンに音声モデルが登録されていません")

        self._default_speaker = speaker
        self._loaded = True
        log.info("tts.loaded", provider=self.id, speaker=speaker)

    async def unload(self) -> None:
        """**Only stops an engine Lumi itself started** (`EngineProcess` decides)."""
        self._loaded = False
        await self._engine.stop()

    def is_loaded(self) -> bool:
        return self._loaded

    def default_voice(self) -> VoiceConfig:
        """Default while there's no Content Pack. **Moves to `voice.toml` later in Phase 1.**"""
        if self._default_speaker is None:
            raise ProviderUnavailable("not_loaded", "load() の前に声を決められない")
        return VoiceConfig(speaker=self._default_speaker, name=self._speaker_name)

    async def synthesize(
        self, text: str, voice: VoiceConfig, cancel_token: CancelToken
    ) -> SpeechAudio:
        """**Synthesis itself is `non_cancellable`.**

        `cancel_token` is checked **before starting** to see whether it's still worth
        doing. A single sentence's synthesis finishes in a few hundred ms, so "generate
        it but don't play it" (discarded by the caller) is simpler and faster than
        aborting mid-synthesis.
        """
        if cancel_token.is_set:
            raise ProviderFailed("cancelled", cancel_token.reason or "")
        try:
            return await self._client.synthesize(
                text, voice.speaker, volume_scale=voice.volume_scale
            )
        except TtsError as error:
            raise ProviderFailed(error.reason, error.detail) from error

    def supported_languages(self) -> frozenset[str]:
        return frozenset({"ja"})

    def resource_hint(self) -> ResourceHint:
        """**Zero VRAM.** This is the primary reason TTS was chosen this way (ADR-008 / DESIGN.md
        §7).
        """
        return ResourceHint(
            # Still a separate process — but no longer free of VRAM (ADR-025).
            # Measured 2026-08-16: 1020 MiB when running on the GPU
            device_pref=DevicePref.EXTERNAL_PROCESS,
            vram_estimate_mb=0 if self._device is DeviceChoice.CPU else 1020,
            load_time_estimate_ms=int(START_TIMEOUT_S * 1000),
            unload_policy=UnloadPolicy.PINNED,
        )

    def attribution(self) -> Attribution:
        """The engine's credit.

        **What this does not guarantee**: what's returned here is the engine's
        information, **not the full license text of the voice model in use.** The full
        text lives in the model's own manifest (docs/licensing.md §4.4); reading it is
        added from Phase 2 onward. Phase 0's credits screen statically embeds the
        default model's full ACML 1.0 text.
        """
        return Attribution(
            display_name="AivisSpeech",
            credit_text="TTS: AivisSpeech Engine",
            license_name="LGPL-3.0",
            license_url="https://github.com/Aivis-Project/AivisSpeech-Engine/blob/master/LICENSE",
            homepage_url="https://aivis-project.com",
        )
