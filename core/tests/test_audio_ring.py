"""The ring buffer. **The foundation of the audio callback.**

docs/architecture/audio.md §3
"""

from __future__ import annotations

import numpy as np
import pytest

from lumi.audio.resample import StreamingResampler, pcm16_to_float32, resample, to_mono
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
    """**Never a partial read.** An oddly sized window reaching VAD breaks inference."""
    ring = RingBuffer(8)
    ring.write(samples(1, 2))
    assert ring.read(3) is None
    # Confirms nothing was dropped
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
    """**Prioritizes newer audio.** But counts what got dropped (never silently degrades)."""
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
    """**Zero-filling isn't an error condition.** TTS just hasn't generated yet."""
    ring = RingBuffer(8)
    ring.write(samples(1, 2))
    out = np.zeros(4, dtype=np.float32)
    filled = ring.read_into(out)
    assert filled == 2
    assert list(out) == [1, 2, 0, 0]


def test_clear_discards_pending_playback() -> None:
    """**Prevents stale audio from resuming after a barge-in.**"""
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


# ── Resampling ──────────────────────────────────────────────


def test_resample_is_a_no_op_at_the_same_rate() -> None:
    x = samples(1, 2, 3)
    assert resample(x, 16000, 16000) is x or list(resample(x, 16000, 16000)) == [1, 2, 3]


def test_downsampling_48k_to_16k_thirds_the_length() -> None:
    """**Streams can't be opened at 16 kHz** (WASAPI shared mode). Hence the conversion here."""
    x = np.zeros(4800, dtype=np.float32)
    assert len(resample(x, 48000, 16000)) == 1600


def tone(frequency: float, rate: int, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(rate * seconds), dtype=np.float64) / rate
    return np.sin(2 * np.pi * frequency * t).astype(np.float32)


def level_db(signal: np.ndarray) -> float:
    return 20.0 * float(np.log10(max(float(np.sqrt(np.mean(signal**2))), 1e-12)))


def test_downsampling_preserves_a_low_frequency_tone() -> None:
    """The passband comes through **at its original level** (the FIR has unity DC gain)."""
    out = resample(tone(220.0, 48000), 48000, 16000)
    # Trimmed: the very edges are the filter ramping up / flushing out
    assert 0.98 < float(np.max(np.abs(out[200:-200]))) < 1.02


def test_downsampling_rejects_content_above_the_new_nyquist() -> None:
    """**The bug that made STT accuracy bad** 〔2026-08-17〕.

    A 3-tap moving average left 9–12 kHz folding back into 4–7 kHz at only −6 to −11 dB.
    Japanese fricatives (し / す / つ) live there, so every sibilant got a mirror image
    laid on top of the consonant band. **Anything above 8 kHz has to disappear, not move.**
    """
    for frequency in (9000.0, 10000.0, 12000.0, 18000.0):
        source = tone(frequency, 48000)
        attenuation = level_db(resample(source, 48000, 16000)) - level_db(source)
        assert attenuation < -40.0, f"{frequency} Hz aliased back at {attenuation:.1f} dB"


def test_upsampling_lengthens() -> None:
    x = np.zeros(1600, dtype=np.float32)
    assert len(resample(x, 16000, 48000)) == 4800


def test_streaming_matches_converting_the_whole_buffer_at_once() -> None:
    """**The VAD thread converts one 32 ms chunk at a time.**

    A stateless converter restarts its filter at every boundary, putting a discontinuity
    into the signal 31 times a second (22.8 dB SNR, measured 2026-08-17). `StreamingResampler`
    carries the filter history, so chunked and one-shot must agree.
    """
    rate, chunk = 48000, 512 * 48000 // 16000
    signal = np.zeros(int(rate * 0.5), dtype=np.float32)
    for k in range(1, 30):
        signal += tone(220.0 * k, rate, 0.5) / k

    converter = StreamingResampler(rate, 16000)
    chunked = np.concatenate(
        [converter.process(signal[i : i + chunk]) for i in range(0, len(signal) - chunk + 1, chunk)]
    )
    whole = resample(signal, rate, 16000)

    # `resample` compensates the filter's group delay; the streaming path deliberately does not
    delay = round((128 - 1) / 2 * 16000 / rate)
    aligned = chunked[delay:]
    count = min(len(aligned), len(whole)) - 100
    error = aligned[:count] - whole[:count]
    assert level_db(error) - level_db(whole[:count]) < -60.0


def test_streaming_produces_one_vad_window_per_ring_read() -> None:
    """32 ms in must be 32 ms out, **every time** — otherwise `_pending` drifts."""
    converter = StreamingResampler(48000, 16000)
    chunk = np.zeros(1536, dtype=np.float32)
    assert [len(converter.process(chunk)) for _ in range(10)] == [512] * 10


def test_streaming_handles_a_rate_that_is_not_an_integer_ratio() -> None:
    """44.1 kHz devices exist. **Nothing may assume the ratio is a whole number.**"""
    converter = StreamingResampler(44100, 16000)
    assert (converter.up, converter.down) == (160, 441)
    produced = sum(len(converter.process(np.zeros(1411, dtype=np.float32))) for _ in range(31))
    assert abs(produced - round(31 * 1411 * 16000 / 44100)) <= 1


def test_reset_clears_the_filter_tail() -> None:
    """**Leftover tail from a previous stream smears the new one.**"""
    converter = StreamingResampler(48000, 16000)
    converter.process(np.ones(4800, dtype=np.float32))
    converter.reset()
    assert float(np.max(np.abs(converter.process(np.zeros(4800, dtype=np.float32))))) == 0.0


def test_to_mono_averages_channels() -> None:
    """**Never takes just one channel.** Devices with a silent channel exist in the wild."""
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
