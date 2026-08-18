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
from lumi.providers.device import DeviceChoice, resolve
from lumi.providers.stt.base import AudioBuffer, Segment, Transcription
from lumi.setup.install import is_model_installed
from lumi.setup.models import STT_MODELS, model_directory

log = lumi_logging.get_logger(__name__)

#: Compute type per device. DESIGN.md §7 places STT on the GPU when there is room.
#: Measured 2026-08-16: **GPU 60 ms vs CPU 920 ms** for the same clip — 15x, for 0.4 GB
#: (docs/measurements/phase1.md). The 0.22 s budget is unreachable on CPU.
#:
#: **`int8_float16` rather than `float16` for large-v3-turbo** 〔2026-08-17 実測 → ADR-027〕:
#: identical CER (3.6%), slightly faster (p50 143 vs 150 ms), and **half the VRAM**
#: (1.0 vs 1.9 GB). There is nothing to buy by keeping the weights in float16.
COMPUTE_TYPE: Final = {DeviceChoice.CUDA: "int8_float16", DeviceChoice.CPU: "int8"}

#: VRAM per model on CUDA, measured (docs/measurements/phase1.md). **Not derived from the
#: file size** — CTranslate2 quantizes on load, so the two are not proportional.
#:
#: **The upper end of what was measured**, not the average. large-v3-turbo came in at
#: 1024 and 1243 MiB across two runs (the spread is cuBLAS workspace, which depends on
#: the clip); a planner that reserves the average discovers the difference as an
#: allocation failure in the middle of a turn.
VRAM_ESTIMATE_MB: Final = {"large-v3-turbo": 1280, "small": 420}

#: Fallback for a model with no measurement. **Deliberately the larger figure**: a planner
#: that under-reserves VRAM gets an allocation failure mid-turn, which is far worse than
#: reserving too much.
DEFAULT_VRAM_ESTIMATE_MB: Final = 1280

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
        device: DeviceChoice | str = DeviceChoice.AUTO,
        compute_type: str | None = None,
        beam_size: int = DEFAULT_BEAM_SIZE,
    ) -> None:
        self.id = f"faster-whisper:{model_size}"
        self._size = model_size
        self._model_dir = model_dir
        #: **Resolved once, at construction.** Probing per request would make the device a
        #: thing that can change mid-session, which nothing downstream expects
        self._device = resolve(device)
        self._compute_type = compute_type or COMPUTE_TYPE[self._device]
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
        log.info("stt.loaded", provider=self.id, device=self._device.value)

    def _build(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:  # pragma: no cover - unreachable if the dependency is present
            raise ProviderNotConfigured("faster_whisper_missing", str(error)) from error

        source = self._resolve()
        try:
            return WhisperModel(
                str(source),
                device=self._device.value,
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

    def _resolve(self) -> Path:
        """The directory the pinned model was installed into (docs/architecture/setup.md §3b).

        **Checked here rather than left to the library.** `faster_whisper` would report a
        missing model as a generic cache miss, and the error would point at the audio path
        instead of at setup.
        """
        artifact = STT_MODELS.get(self._size)
        if artifact is None:
            raise ProviderNotConfigured("unknown_model", f"ピン留めされていない: {self._size}")
        if not is_model_installed(artifact, self._model_dir):
            raise ProviderNotConfigured(
                "model_missing",
                f"{self._size} が {self._model_dir} に無い（セットアップで取得してください）",
            )
        return model_directory(artifact, self._model_dir)

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
                DevicePref.CPU_ONLY
                if self._device is DeviceChoice.CPU
                else DevicePref.GPU_PREFERRED
            ),
            vram_estimate_mb=(
                0
                if self._device is DeviceChoice.CPU
                else VRAM_ESTIMATE_MB.get(self._size, DEFAULT_VRAM_ESTIMATE_MB)
            ),
            # Measured 2026-08-17: large-v3-turbo 2.4 s including warmup, small 0.8 s
            load_time_estimate_ms=3000,
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
