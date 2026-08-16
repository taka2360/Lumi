"""Sample rate conversion. **Pure functions** (never touches a device).

Design → docs/architecture/audio.md §8

## Why this is needed

**Streams can't be opened at 16 kHz.** WASAPI shared mode only accepts the endpoint's
mix format rate (usually 48 kHz) (observed in Phase 0). VAD and STT, meanwhile, assume
16 kHz. **This bridges the gap inside Core.**

## Where the conversion happens

**In the VAD thread. Never in the audio callback.**
Conversion involves memory allocation, which threatens the callback's deadline (a few ms).
The callback writes to the ring at the device's native rate, and the reader converts.

## On quality (being honest)

What's here is a simple **moving average + linear interpolation** implementation.
Alias suppression is weaker than a polyphase FIR (soxr / scipy).

**This was chosen to avoid adding a dependency for now — it isn't a claim that the
quality is good enough.** If Step F's measurements show an impact on STT accuracy,
add `soxr` (~300 KB). Kept inside **a single pure function** so that swap is the only
thing that needs to change.
"""

from __future__ import annotations

import numpy as np

from lumi.audio.ring import Samples


def resample(x: Samples, src_rate: int, dst_rate: int) -> Samples:
    """Convert a waveform at `src_rate` to `dst_rate`.

    When downsampling, **a moving average is applied first**. Without it, content above
    the Nyquist frequency aliases down into the low end, which can make VAD misjudge
    "there's a voice."
    """
    if src_rate == dst_rate or len(x) == 0:
        return x.astype(np.float32, copy=False)
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("サンプルレートは正の数")

    work = x.astype(np.float32, copy=False)

    if src_rate > dst_rate:
        taps = int(src_rate // dst_rate)
        if taps >= 2:
            kernel = np.ones(taps, dtype=np.float32) / float(taps)
            work = np.convolve(work, kernel, mode="same").astype(np.float32)

    count = round(len(work) * dst_rate / src_rate)
    if count <= 0:
        return np.zeros(0, dtype=np.float32)

    source_index = np.linspace(0.0, len(work) - 1.0, count, dtype=np.float64)
    resampled = np.interp(source_index, np.arange(len(work), dtype=np.float64), work)
    return resampled.astype(np.float32)


def to_mono(x: Samples, channels: int) -> Samples:
    """Collapse interleaved multi-channel audio to mono. **Averages the channels.**

    Taking just one channel would cause "should be audible but isn't" on devices where
    that particular channel is silent (these exist in the wild).
    """
    if channels <= 1:
        return x.astype(np.float32, copy=False)
    usable = (len(x) // channels) * channels
    if usable == 0:
        return np.zeros(0, dtype=np.float32)
    return x[:usable].reshape(-1, channels).mean(axis=1).astype(np.float32)


def to_interleaved(mono: Samples, channels: int) -> Samples:
    """Expand mono to the output stream's channel count. **Copies the same waveform to every channel.**

    TTS output is mono, but the output stream is opened with the device's default
    channel count (docs/architecture/audio.md §8). Putting it in only one channel would
    make it audible from only one ear.
    """
    if channels <= 1:
        return mono.astype(np.float32, copy=False)
    return np.repeat(mono.astype(np.float32, copy=False), channels)


def pcm16_to_float32(data: bytes) -> Samples:
    """Convert 16-bit PCM (TTS engine output) to float32. **Normalized to -1.0..1.0.**"""
    if not data:
        return np.zeros(0, dtype=np.float32)
    ints = np.frombuffer(data, dtype="<i2")
    return (ints.astype(np.float32) / 32768.0).astype(np.float32)
