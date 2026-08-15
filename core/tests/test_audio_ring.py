"""リングバッファ。**オーディオコールバックの土台。**

docs/architecture/audio.md §3
"""

from __future__ import annotations

import numpy as np
import pytest

from lumi.audio.resample import pcm16_to_float32, resample, to_mono
from lumi.audio.ring import RingBuffer


def samples(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def test_write_then_read_round_trips() -> None:
    ring = RingBuffer(8)
    ring.write(samples(1, 2, 3))
    out = ring.read(3)
    assert out is not None
    assert list(out) == [1, 2, 3]


def test_read_refuses_a_partial_window() -> None:
    """**部分読みをしない。** 半端なサイズの窓が VAD に渡ると推論が壊れる。"""
    ring = RingBuffer(8)
    ring.write(samples(1, 2))
    assert ring.read(3) is None
    # 捨てられていないこと
    ring.write(samples(3))
    out = ring.read(3)
    assert out is not None
    assert list(out) == [1, 2, 3]


def test_wrapping_is_contiguous() -> None:
    ring = RingBuffer(4)
    ring.write(samples(1, 2, 3))
    ring.read(3)
    ring.write(samples(4, 5, 6))
    out = ring.read(3)
    assert out is not None
    assert list(out) == [4, 5, 6]


def test_overflow_drops_the_oldest_and_counts_it() -> None:
    """**新しい音を優先する。** ただし捨てたことは数える（黙って劣化しない）。"""
    ring = RingBuffer(4)
    ring.write(samples(1, 2, 3, 4))
    ring.write(samples(5, 6))
    assert ring.dropped == 2
    out = ring.read(4)
    assert out is not None
    assert list(out) == [3, 4, 5, 6]


def test_a_write_larger_than_the_ring_keeps_the_tail() -> None:
    ring = RingBuffer(3)
    ring.write(samples(1, 2, 3, 4, 5))
    out = ring.read(3)
    assert out is not None
    assert list(out) == [3, 4, 5]


def test_read_into_pads_with_silence() -> None:
    """**0 埋めは異常ではない。** TTS がまだ生成していないだけ。"""
    ring = RingBuffer(8)
    ring.write(samples(1, 2))
    out = np.zeros(4, dtype=np.float32)
    filled = ring.read_into(out)
    assert filled == 2
    assert list(out) == [1, 2, 0, 0]


def test_clear_discards_pending_playback() -> None:
    """**barge-in の後、古い音が再開しないように。**"""
    ring = RingBuffer(8)
    ring.write(samples(1, 2, 3))
    ring.clear()
    assert ring.available == 0
    out = np.ones(2, dtype=np.float32)
    assert ring.read_into(out) == 0
    assert list(out) == [0, 0]


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="capacity"):
        RingBuffer(0)


# ── リサンプル ──────────────────────────────────────────────


def test_resample_is_a_no_op_at_the_same_rate() -> None:
    x = samples(1, 2, 3)
    assert resample(x, 16000, 16000) is x or list(resample(x, 16000, 16000)) == [1, 2, 3]


def test_downsampling_48k_to_16k_thirds_the_length() -> None:
    """**16 kHz でストリームは開けない**（WASAPI 共有モード）。だからここで変換する。"""
    x = np.zeros(4800, dtype=np.float32)
    assert len(resample(x, 48000, 16000)) == 1600


def test_downsampling_preserves_a_low_frequency_tone() -> None:
    """折り返しで低域が壊れないこと（移動平均を前置している理由）。"""
    rate = 48000
    t = np.arange(rate, dtype=np.float32) / rate
    tone = np.sin(2 * np.pi * 220.0 * t).astype(np.float32)

    out = resample(tone, rate, 16000)

    # 振幅がだいたい保たれている（移動平均でわずかに減る）
    assert 0.7 < float(np.max(np.abs(out))) <= 1.0


def test_upsampling_lengthens() -> None:
    x = np.zeros(1600, dtype=np.float32)
    assert len(resample(x, 16000, 48000)) == 4800


def test_to_mono_averages_channels() -> None:
    """**片方だけ採らない。** 無音のチャンネルを持つデバイスが実在する。"""
    interleaved = samples(1, 3, 2, 4)
    assert list(to_mono(interleaved, 2)) == [2, 3]


def test_to_mono_ignores_a_trailing_partial_frame() -> None:
    assert list(to_mono(samples(1, 3, 2), 2)) == [2]


def test_pcm16_is_normalised() -> None:
    data = np.array([0, 32767, -32768], dtype="<i2").tobytes()
    out = pcm16_to_float32(data)
    assert out[0] == 0.0
    assert 0.99 < out[1] <= 1.0
    assert out[2] == -1.0


def test_empty_inputs_are_handled() -> None:
    empty = np.zeros(0, dtype=np.float32)
    assert len(resample(empty, 48000, 16000)) == 0
    assert len(to_mono(empty, 2)) == 0
    assert len(pcm16_to_float32(b"")) == 0
