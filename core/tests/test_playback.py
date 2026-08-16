"""WAV のデコード。**純粋関数**（デバイスに触らない）。

再生そのものの試験は `test_agent_speech.py`（PlaybackScheduler）と
`test_audio_ring.py`（リング）にある。Phase 0 の `play_wav` は
止める手段が無く barge-in が成立しないので、Step E で消えた。
"""

from __future__ import annotations

import io
import wave

import pytest

from lumi.audio.wav import WavError, decode_wav


def wav_bytes(*, channels: int = 1, width: int = 2, rate: int = 44100, frames: int = 100) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(channels)
        sink.setsampwidth(width)
        sink.setframerate(rate)
        sink.writeframes(b"\x00" * frames * channels * width)
    return buffer.getvalue()


def test_reads_the_format_from_the_header() -> None:
    wav = decode_wav(wav_bytes(channels=2, rate=24000))
    assert (wav.channels, wav.sample_width, wav.sample_rate) == (2, 2, 24000)
    assert len(wav.frames) == 100 * 2 * 2


def test_reports_the_duration() -> None:
    # **リップシンクの時間割りに使う**（エンジンが音素長を返さないため）。
    wav = decode_wav(wav_bytes(rate=1000, frames=1500))
    assert wav.duration_seconds == pytest.approx(1.5)


def test_rejects_a_broken_file() -> None:
    # **エンジンの出力を信用しない**（Invariant 3）。
    with pytest.raises(WavError):
        decode_wav(b"not a wav at all")
