"""faster-whisper (CTranslate2, int8). **Does not depend on torch** (R1).

Decision → ADR-023 / Design → docs/interfaces/provider.md

## Stopping the library's automatic download

`WhisperModel("small")`, if the model isn't cached, **silently fetches it from Hugging
Face.** The user hasn't chosen anything, no progress is shown, and the failure reason
never enters Lumi's vocabulary.

ADR-019's decision — "never fetch externally without consent" — **gets bypassed by the
library's default.** So `local_files_only=True` is pinned, and **failure is explicit
when the model is missing.** Fetching is consolidated into Lumi's own setup path
(pinning + SHA-256 + rollback).

## Why the import is deferred

Importing `faster_whisper` pulls in `ctranslate2` / `onnxruntime` / `tokenizers`, and
**visibly slows startup.** A startup that doesn't use voice input (TTS-only, or no
device) shouldn't have to pay that cost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from lumi import logging as lumi_logging
from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import (
    Attribution,
    DevicePref,
    ProviderFailed,
    ProviderKind,
    ProviderNotConfigured,
    ResourceHint,
    UnloadPolicy,
)
from lumi.providers.stt.base import AudioBuffer, Segment, Transcription

log = lumi_logging.get_logger(__name__)

#: **CPU / int8.** The GPU is dedicated entirely to the LLM (DESIGN.md §7)
DEFAULT_COMPUTE_TYPE: Final = "int8"
DEFAULT_DEVICE: Final = "cpu"

#: Tuned after measurement [Provisional]. SLO is p50 0.22 s (docs/architecture/audio.md §7)
DEFAULT_BEAM_SIZE: Final = 1


class FasterWhisperProvider:
    """Implementation of `STTProvider`."""

    kind = ProviderKind.STT

    __slots__ = ("_beam_size", "_compute_type", "_device", "_model", "_model_dir", "_size", "id")

    def __init__(
        self,
        model_size: str,
        model_dir: Path,
        *,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        beam_size: int = DEFAULT_BEAM_SIZE,
    ) -> None:
        self.id = f"faster-whisper:{model_size}"
        self._size = model_size
        self._model_dir = model_dir
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._model: Any | None = None

    async def load(self) -> None:
        """**Idempotent.** Raises `ProviderNotConfigured` if the model is missing (never fetches
        it).
        """
        if self._model is not None:
            return

        import asyncio

        self._model = await asyncio.to_thread(self._build)
        log.info("stt.loaded", provider=self.id, device=self._device)

    def _build(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:  # pragma: no cover - unreachable if the dependency is present
            raise ProviderNotConfigured("faster_whisper_missing", str(error)) from error

        try:
            return WhisperModel(
                self._size,
                device=self._device,
                compute_type=self._compute_type,
                download_root=str(self._model_dir),
                # * **This is ADR-023's implementation.** Fails if missing
                local_files_only=True,
            )
        except Exception as error:
            # The library raises various exceptions. **Translated into "model missing" here**
            raise ProviderNotConfigured(
                "model_missing",
                f"{self._size} が {self._model_dir} に無い（セットアップで取得してください）",
            ) from error

    async def unload(self) -> None:
        self._model = None

    def is_loaded(self) -> bool:
        return self._model is not None

    async def transcribe(
        self, audio: AudioBuffer, language: str | None, cancel_token: CancelToken
    ) -> Transcription:
        """**Inference is offloaded to a thread** (never blocks the event loop).

        The contract is `cooperative`, but **a single `transcribe` call can't be split.**
        All that's possible is "give up before starting" or discarding the result.
        The caller (Reactive Loop) makes barge-in work by discarding the result.
        """
        if self._model is None:
            raise ProviderNotConfigured("not_loaded", "load() されていない")
        if cancel_token.is_set:
            raise ProviderFailed("cancelled", cancel_token.reason or "")

        import asyncio

        return await asyncio.to_thread(self._transcribe, audio, language)

    def _transcribe(self, audio: AudioBuffer, language: str | None) -> Transcription:
        assert self._model is not None
        try:
            segments, info = self._model.transcribe(
                audio, language=language, beam_size=self._beam_size
            )
            # faster-whisper's segments are lazily evaluated. **Materialized here**
            collected = [
                Segment(start_s=float(s.start), end_s=float(s.end), text=str(s.text))
                for s in segments
            ]
        except Exception as error:
            raise ProviderFailed("transcribe_failed", str(error)) from error

        text = "".join(segment.text for segment in collected).strip()
        return Transcription(
            text=text,
            language=str(getattr(info, "language", language or "")),
            confidence=_probability(info),
            segments=tuple(collected),
        )

    def resource_hint(self) -> ResourceHint:
        return ResourceHint(
            device_pref=(
                DevicePref.CPU_ONLY if self._device == "cpu" else DevicePref.GPU_PREFERRED
            ),
            vram_estimate_mb=0 if self._device == "cpu" else 1000,
            load_time_estimate_ms=2000,
            unload_policy=UnloadPolicy.PINNED,
        )

    def attribution(self) -> Attribution:
        return Attribution(
            display_name="faster-whisper",
            credit_text=f"STT: faster-whisper ({self._size})",
            license_name="MIT",
            license_url="https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE",
            homepage_url="https://github.com/SYSTRAN/faster-whisper",
        )


def _probability(info: Any) -> float | None:
    value = getattr(info, "language_probability", None)
    return float(value) if isinstance(value, int | float) else None
