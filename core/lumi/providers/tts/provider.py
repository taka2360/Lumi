"""`AivisSpeechProvider`. Adapts the existing HTTP client to `TTSProvider`.

Design → docs/interfaces/provider.md / Ownership and lifetime → docs/architecture/core.md §6

## Ownership of the engine process

**Lumi only stops what Lumi itself started.** An already-running engine is left alone
(the user might have started it themselves). This rule is implemented by
`EngineProcess`; the Provider just maps it onto `load()` / `unload()`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Final

from lumi import logging as lumi_logging
from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import (
    Attribution,
    DevicePref,
    EngineRuntime,
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

log = lumi_logging.get_logger(__name__)

# : Grace period before the engine responds. **Never wait forever** (couldn't tell it apart from
# never starting)
START_TIMEOUT_S: Final = 180.0

#: What `_warm` synthesizes and throws away. **Long enough to produce moras** — the engine
#: rejects input it cannot break into any (`aivisspeech.py`: `no_moras`), and a warm-up that
#: fails on every machine would warm nothing while looking like it did.
WARM_TEXT: Final = "あー"


class AivisSpeechProvider:
    """TTS provider backed by a separate process on GPU when available, else CPU."""

    id = "aivisspeech"
    kind = ProviderKind.TTS

    __slots__ = (
        "_client",
        "_default_speaker",
        "_device",
        "_engine",
        "_loaded",
        "_preferred",
        "_speaker_name",
    )

    def __init__(
        self,
        port: int,
        *,
        executable: Path | None = None,
        device: DeviceChoice | str = DeviceChoice.AUTO,
        speaker: int | None = None,
    ) -> None:
        self._client = AivisSpeechClient(port)
        self._engine = EngineProcess(executable, port, device=device)
        self._device = resolve(device)
        #: The Content Pack's choice, or `None` to defer to the engine's default.
        #: **Passed in rather than read here** — the pack belongs to the runtime, and a
        #: Provider that reads it would be deciding which character it is speaking as
        self._preferred = speaker
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
            raise ProviderUnavailable("engine_not_ready", f"Engine runtime status: {runtime}")

        try:
            speaker = self._preferred
            if speaker is None:
                speaker = await self._client.default_speaker()
        except TtsError as error:
            raise ProviderUnavailable(error.reason, error.detail) from error

        if speaker is None:
            # Installed but can't speak = **broken** (different from not-set-up)
            raise ProviderUnavailable("no_speaker", "No voice model registered in TTS engine")

        # **Deciding the speaker is not loading it.** The engine loads the voice model on its
        # first `audio_query`, which put 3092 ms inside `tts_first_audio_ms` on the first
        # sentence (2026-08-18) -> docs/interfaces/provider.md "`load()` is not a connection check"
        started = time.perf_counter()
        try:
            await self._client.initialize_speaker(speaker)
        except TtsError as error:
            # **Slow, not broken.** The engine is answering; the first sentence just pays what
            # it used to. Never let that pass as a warm start
            log.warning("tts.speaker_init_failed", speaker=speaker, detail=error.detail)
        else:
            log.info(
                "tts.speaker_initialized",
                speaker=speaker,
                ms=round((time.perf_counter() - started) * 1000),
            )

        self._default_speaker = speaker
        await self._warm(speaker)
        self._loaded = True
        log.info("tts.loaded", provider=self.id, speaker=speaker)

    async def _warm(self, speaker: int) -> None:
        """Synthesizes one throwaway sentence, so **the next one runs at production latency.**

        **Loading the voice model is not the same as being able to speak.** `initialize_speaker`
        puts the weights in memory; the inference session's own first run is a separate cost,
        and it was still landing on the first sentence — **1579 ms against a ~200 ms steady
        state** (2026-08-22, same shape as the STT side of the same turn). Warming it here
        moves that onto startup, where nobody is waiting.

        **Discarded, never played.** Nothing is on the playback path at load time, and the
        audio exists only to make the engine do the work once.

        Failing is **slow, not broken** — the same judgment as `initialize_speaker` above.
        """
        started = time.perf_counter()
        try:
            await self._client.synthesize(WARM_TEXT, speaker)
        except TtsError as error:
            log.warning("tts.warmup_synthesis_failed", speaker=speaker, detail=error.detail)
            return
        log.info("tts.warmed", speaker=speaker, ms=round((time.perf_counter() - started) * 1000))

    async def unload(self) -> None:
        """**Only stops an engine Lumi itself started** (`EngineProcess` decides)."""
        self._loaded = False
        await self._engine.stop()

    def is_loaded(self) -> bool:
        return self._loaded

    def default_voice(self) -> VoiceConfig:
        """Default while there's no Content Pack. **Moves to `voice.toml` later in Phase 1.**"""
        if self._default_speaker is None:
            raise ProviderUnavailable("not_loaded", "Cannot determine voice before calling load()")
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
                text,
                voice.speaker,
                volume_scale=voice.volume_scale,
                speed_scale=voice.speed_scale,
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
