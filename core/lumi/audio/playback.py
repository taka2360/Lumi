"""WAV の再生。

設計 → docs/architecture/audio.md

**Phase 0 の範囲はここまで。** duplex stream（capture + playback + reference channel）と
即時ミュートは Phase 0 の Step K / Phase 1 で作る。ここに VAD もミュートも無い。
「今は無い」ことを名前と docstring で明示しておき、**あるように見せない**。

`sounddevice` は PortAudio のバインディング（どちらも MIT 系）。
再生は別スレッドで行い、**イベントループを止めない**。
"""

from __future__ import annotations

import asyncio

from lumi import logging as lumi_logging
from lumi.audio.wav import WavError, decode_wav

log = lumi_logging.get_logger(__name__)


class PlaybackError(RuntimeError):
    """鳴らせなかった。**理由を持つ**（黙って無音にしない）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _play_blocking(data: bytes) -> None:
    """PortAudio に渡して鳴り終わるまで待つ。**専用スレッドで呼ぶこと。**

    バイト列のまま渡す（`RawOutputStream`）。numpy を経由しないので、
    **Phase 0 の依存を1つ増やさずに済む**。numpy が要るのは VAD が入る Phase 1。
    """
    try:
        import sounddevice as sd
    except OSError as error:
        # PortAudio が無い / 開けない環境。**黙って無音にしない。**
        raise PlaybackError("audio_backend_unavailable", str(error)) from error

    try:
        wav = decode_wav(data)
    except WavError as error:
        raise PlaybackError("wav_malformed", str(error)) from error

    if wav.sample_width != 2:
        # エンジンは 16bit PCM を返す。それ以外が来たら、変換を自作せず失敗させる。
        raise PlaybackError("unsupported_sample_width", f"{wav.sample_width * 8}bit")

    try:
        with sd.RawOutputStream(
            samplerate=wav.sample_rate, channels=wav.channels, dtype="int16"
        ) as stream:
            stream.write(wav.frames)
    except Exception as error:  # sounddevice は PortAudioError を投げる
        raise PlaybackError("playback_failed", str(error)) from error


async def play_wav(data: bytes) -> None:
    """WAV を鳴らし終わるまで待つ。"""
    await asyncio.to_thread(_play_blocking, data)
