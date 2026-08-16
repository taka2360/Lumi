"""Decoding WAV. **Pure functions** (never touches a device).

Testing playback itself lives in `test_agent_speech.py` (PlaybackScheduler) and
`test_audio_ring.py` (the ring). Phase 0's `play_wav` had no way to stop and couldn't
support barge-in, so it was removed in Step E.
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
    # **Used for the lip-sync timeline** (since the engine doesn't return phoneme lengths).
    wav = decode_wav(wav_bytes(rate=1000, frames=1500))
    assert wav.duration_seconds == pytest.approx(1.5)


def test_rejects_a_broken_file() -> None:
    # **Never trusts the engine's output** (Invariant 3).
    with pytest.raises(WavError):
        decode_wav(b"not a wav at all")
