"""faster-whisper（CTranslate2, int8）。**torch に依存しない**（R1）。

決定 → ADR-023 / 設計 → docs/interfaces/provider.md

## ライブラリの自動ダウンロードを止める

`WhisperModel("small")` は、モデルがキャッシュに無ければ **黙って Hugging Face から
取りに行く。** ユーザーは何も選んでいないし、進捗も出ないし、失敗理由も Lumi の語彙にならない。

ADR-019 で決めた「同意なしに外部へ取りに行かない」が、**ライブラリの既定値によって
迂回される。** そこで `local_files_only=True` を固定し、**無ければ明示的に失敗させる。**
取得は Lumi のセットアップ経路（ピン留め + SHA-256 + ロールバック）に一本化する。

## import を遅らせる理由

`faster_whisper` の import は `ctranslate2` / `onnxruntime` / `tokenizers` を引き込み、
**起動が目に見えて遅くなる。** 音声入力を使わない起動（TTS だけ、あるいはデバイスが無い）
でその代償を払う必要はない。
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

#: **CPU / int8。** GPU は LLM に全振りする（DESIGN.md §7）
DEFAULT_COMPUTE_TYPE: Final = "int8"
DEFAULT_DEVICE: Final = "cpu"

#: 実測して調整する〔Provisional〕。SLO は p50 0.22 s（docs/architecture/audio.md §7）
DEFAULT_BEAM_SIZE: Final = 1


class FasterWhisperProvider:
    """`STTProvider` の実装。"""

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
        """**冪等。** モデルが無ければ `ProviderNotConfigured`（取りに行かない）。"""
        if self._model is not None:
            return

        import asyncio

        self._model = await asyncio.to_thread(self._build)
        log.info("stt.loaded", provider=self.id, device=self._device)

    def _build(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:  # pragma: no cover - 依存が入っていれば起きない
            raise ProviderNotConfigured("faster_whisper_missing", str(error)) from error

        try:
            return WhisperModel(
                self._size,
                device=self._device,
                compute_type=self._compute_type,
                download_root=str(self._model_dir),
                # ★ **ここが ADR-023 の実装。** 無ければ落ちる
                local_files_only=True,
            )
        except Exception as error:
            # ライブラリは種々の例外を投げる。**「モデルが無い」に翻訳して返す**
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
        """**推論はスレッドに逃がす**（イベントループを止めない）。

        契約は `cooperative` だが、**1回の `transcribe` は分割できない。**
        できるのは「始める前に諦める」ことと、結果を捨てることだけ。
        呼び出し側（Reactive Loop）は結果を捨てる側で barge-in を成立させる。
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
            # faster-whisper のセグメントは遅延評価。**ここで確定させる**
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
