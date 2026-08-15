"""再生の入口。**音は鳴らさない**（デバイスに触れる手前まで）。

docs/architecture/audio.md — Phase 0 の範囲は再生だけ。
"""

from __future__ import annotations

import io
import wave

import pytest

from lumi.audio.playback import PlaybackError, _play_blocking
from lumi.audio.wav import WavError, decode_wav


def wav_bytes(*, channels: int = 1, width: int = 2, rate: int = 44100, frames: int = 100) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(channels)
        sink.setsampwidth(width)
        sink.setframerate(rate)
        sink.writeframes(b"\x00" * frames * channels * width)
    return buffer.getvalue()


class TestDecode:
    def test_reads_the_format_from_the_header(self) -> None:
        wav = decode_wav(wav_bytes(channels=2, rate=24000))
        assert (wav.channels, wav.sample_width, wav.sample_rate) == (2, 2, 24000)
        assert len(wav.frames) == 100 * 2 * 2

    def test_reports_the_duration(self) -> None:
        # **リップシンクの時間割りに使う**（エンジンが音素長を返さないため）。
        wav = decode_wav(wav_bytes(rate=1000, frames=1500))
        assert wav.duration_seconds == pytest.approx(1.5)

    def test_rejects_a_broken_file(self) -> None:
        # **エンジンの出力を信用しない**（Invariant 3）。
        with pytest.raises(WavError):
            decode_wav(b"not a wav at all")

    def test_playback_reports_a_broken_file_as_its_own_reason(self) -> None:
        with pytest.raises(PlaybackError) as error:
            _play_blocking(b"not a wav at all")
        assert error.value.reason == "wav_malformed"


class TestUnsupportedFormat:
    def test_refuses_to_convert_sample_width_itself(self) -> None:
        """8bit が来たら**変換を自作せず失敗させる**。

        黙って変換すると、音が壊れた理由が分からなくなる。
        デバイスに触る前に弾くので、このテストは音を鳴らさない。
        """
        with pytest.raises(PlaybackError) as error:
            _play_blocking(wav_bytes(width=1))
        assert error.value.reason == "unsupported_sample_width"
