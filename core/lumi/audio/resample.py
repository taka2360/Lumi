"""Sample rate conversion. **Pure functions + one streaming object** (never touches a device).

Design → docs/architecture/audio.md §8

## Why this is needed

**Streams can't be opened at 16 kHz.** WASAPI shared mode only accepts the endpoint's
mix format rate (usually 48 kHz) (observed in Phase 0). VAD and STT, meanwhile, assume
16 kHz. **This bridges the gap inside Core.**

## Where the conversion happens

**In the VAD thread. Never in the audio callback.**
Conversion involves memory allocation, which threatens the callback's deadline (a few ms).
The callback writes to the ring at the device's native rate, and the reader converts.

## ★ Why this is a polyphase FIR now 〔2026-08-17 measured〕

The first implementation was a 3-tap moving average plus linear interpolation. Measured
on the 48 kHz → 16 kHz path it turned out to be **the reason STT accuracy was bad**:

| Input | Where its energy landed | Attenuation |
|---|---|---|
| 9 kHz | **7 kHz** | −5.6 dB |
| 10 kHz | **6 kHz** | −7.2 dB |
| 12 kHz | **4 kHz** | −11.3 dB |

Everything above the 8 kHz Nyquist folded straight back into the speech band essentially
unattenuated. Japanese fricatives (し / す / つ / しゅ) carry most of their energy at
5–12 kHz, so **a mirror image of every sibilant was laid on top of the consonant
discrimination band.** On top of that, the VAD thread called the function once per 32 ms
chunk, and a stateless converter cannot be continuous across chunks — measured at
**22.8 dB SNR** against the same signal converted in one piece, i.e. a discontinuity
every 512 output samples.

So the converter is now:

- a **polyphase FIR** (Kaiser window, ~70 dB stopband) — the alias images are gone
- **stateful across calls** (`StreamingResampler`) — no per-chunk discontinuity

`soxr` was the option named in the earlier note, but **libsoxr is LGPL-2.1** and Core is
MIT and shipped as one PyInstaller bundle (docs/licensing.md). ~60 lines of numpy costs
less than that boundary does.

**What this does not guarantee**: the filter is 128 taps per phase, so the transition
band is roughly 6.4 kHz → 8 kHz. Content just under Nyquist is attenuated on purpose;
that is the trade for killing the images.
"""

from __future__ import annotations

from math import gcd
from typing import Final

import numpy as np

from lumi.audio.ring import Samples

#: Taps per polyphase phase. **The cost per output sample, independent of the rate pair.**
#: 128 buys ~70 dB of stopband at a 6.4 → 8 kHz transition; 512 outputs cost ~65k MACs,
#: which is nothing against the VAD thread's 32 ms budget.
TAPS_PER_PHASE: Final = 128

#: Cutoff as a fraction of the lower Nyquist. **Below 1.0 on purpose** — the transition band
#: has to fit somewhere, and losing a little at 7 kHz beats aliasing at 6 kHz.
CUTOFF_RATIO: Final = 0.92

#: Kaiser β for ~70 dB stopband attenuation (0.1102 * (A - 8.7)).
KAISER_BETA: Final = 6.76

#: Cap on the interpolation factor. Standard device rates (48 / 44.1 / 32 / 22.05 kHz) all
#: land well under this; **an exotic rate is approximated rather than allowed to allocate a
#: multi-megabyte filter.**
MAX_INTERPOLATION: Final = 512


def _design(up: int, down: int) -> np.ndarray:
    """Windowed-sinc lowpass, folded into `up` polyphase phases.

    Returns shape `(up, TAPS_PER_PHASE)` with **the taps already reversed**, so one output
    sample is `phase_row · input_window`.
    """
    total = up * TAPS_PER_PHASE
    # In the upsampled domain the sample rate is `up` times the input rate. The passband has
    # to stop below *both* Nyquists, which is the lower rate's — hence `min`.
    cutoff = 0.5 * CUTOFF_RATIO * min(1.0, up / down) / up
    n = np.arange(total, dtype=np.float64)
    center = (total - 1) / 2.0
    taps = 2.0 * cutoff * np.sinc(2.0 * cutoff * (n - center)) * np.kaiser(total, KAISER_BETA)
    # Zero-stuffing by `up` divides amplitude by `up`. **Compensated here**, so a DC input
    # comes out at the same level it went in.
    taps *= up
    # `taps[phase + up * i]` → `[phase, i]`, then reversed so the dot product reads
    # `sum_i row[i] * window[i]` with the window in natural order.
    return taps.reshape(TAPS_PER_PHASE, up).T[:, ::-1].astype(np.float32)


def _ratio(src_rate: int, dst_rate: int) -> tuple[int, int]:
    """`(up, down)` in lowest terms, with `up` capped (`MAX_INTERPOLATION`)."""
    divisor = gcd(src_rate, dst_rate)
    up, down = dst_rate // divisor, src_rate // divisor
    if up <= MAX_INTERPOLATION:
        return up, down
    # Approximate. **The rate error is under 1/MAX_INTERPOLATION** — inaudible, and far
    # cheaper than a filter with tens of thousands of taps.
    from fractions import Fraction

    approx = Fraction(dst_rate, src_rate).limit_denominator(MAX_INTERPOLATION)
    return max(1, approx.numerator), max(1, approx.denominator)


class StreamingResampler:
    """Rate conversion that is **continuous across calls.**

    The VAD thread hands it one ring read at a time (32 ms). A stateless converter restarts
    its filter at every chunk boundary, which puts a discontinuity into the signal 31 times
    a second — measured at 22.8 dB SNR before this existed.

    **Not thread-safe.** One instance belongs to one reader (the VAD thread).

    Output lags the input by `TAPS_PER_PHASE / 2` input samples (~1.3 ms at 48 kHz).
    That is left uncompensated: it is far under one VAD frame (32 ms) and constant, so it
    shifts no measurement relative to any other.
    """

    __slots__ = ("_base", "_down", "_history", "_position", "_taps", "_up")

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("サンプルレートは正の数")
        self._up, self._down = _ratio(src_rate, dst_rate)
        self._taps = _design(self._up, self._down)
        self.reset()

    @property
    def up(self) -> int:
        return self._up

    @property
    def down(self) -> int:
        return self._down

    def reset(self) -> None:
        """Discard filter history. **Leftover tail from a previous stream smears the new one.**"""
        #: Input samples still needed as filter history, covering global indices
        #: `[_base, _base + len(_history))`. Primed with zeros so the first real sample is
        #: already `TAPS_PER_PHASE - 1` deep and no output has to be withheld.
        self._history: Samples = np.zeros(TAPS_PER_PHASE - 1, dtype=np.float32)
        self._base = -(TAPS_PER_PHASE - 1)
        #: Position of the next output sample in the upsampled domain.
        self._position = 0

    def process(self, x: Samples) -> Samples:
        """Convert one chunk. **Returns however many samples are ready** (may be empty)."""
        if self._up == self._down:
            return x.astype(np.float32, copy=False)

        buffer = (
            self._history
            if len(x) == 0
            else np.concatenate((self._history, x.astype(np.float32, copy=False)))
        )
        up, down = self._up, self._down
        last = self._base + len(buffer) - 1

        # How many outputs can be produced before an output needs an input we don't have.
        # Output k sits at `position + k * down` in the upsampled domain, and reads input
        # index `(position + k * down) // up`.
        room = up * (last + 1) - 1 - self._position
        count = 0 if room < 0 else room // down + 1

        if count == 0:
            self._history = buffer
            return np.zeros(0, dtype=np.float32)

        upsampled = self._position + np.arange(count, dtype=np.int64) * down
        newest = upsampled // up
        # Window `j` of the view covers `buffer[j : j + TAPS_PER_PHASE]`, i.e. global
        # `[base + j, base + j + TAPS_PER_PHASE - 1]`. We want it to end at `newest`.
        starts = newest - self._base - (TAPS_PER_PHASE - 1)
        windows = np.lib.stride_tricks.sliding_window_view(buffer, TAPS_PER_PHASE)[starts]

        if up == 1:
            # The common case (48 → 16 kHz is 1/3). One matvec, no per-sample tap gather.
            out = windows @ self._taps[0]
        else:
            out = np.einsum("ij,ij->i", windows, self._taps[upsampled % up])

        self._position += count * down
        # Keep exactly the history the next call needs. `_position // up` is now past `last`,
        # so the tail is enough and the next chunk continues at `base + len(buffer)`.
        self._base += len(buffer) - (TAPS_PER_PHASE - 1)
        self._history = buffer[-(TAPS_PER_PHASE - 1) :].copy()
        return np.asarray(out, dtype=np.float32)


def resample(x: Samples, src_rate: int, dst_rate: int) -> Samples:
    """Convert a whole waveform at `src_rate` to `dst_rate`. **Pure** — no state kept.

    Group delay is compensated and the result is `round(len(x) * dst_rate / src_rate)`
    samples, so this stays a drop-in for "convert this buffer."

    **Use `StreamingResampler` for a continuous stream.** Calling this per chunk restarts
    the filter each time (that was the 22.8 dB SNR bug).
    """
    if src_rate == dst_rate or len(x) == 0:
        return x.astype(np.float32, copy=False)
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("サンプルレートは正の数")

    converter = StreamingResampler(src_rate, dst_rate)
    wanted = round(len(x) * dst_rate / src_rate)
    # The filter's peak sits `(TAPS_PER_PHASE - 1) / 2` input samples behind its output, so
    # that many leading samples are ramp-up and the same amount of tail has to be flushed.
    skip = round((TAPS_PER_PHASE - 1) / 2 * dst_rate / src_rate)
    flush = int((TAPS_PER_PHASE - 1) / 2) + 1

    out = np.concatenate(
        (
            converter.process(x.astype(np.float32, copy=False)),
            converter.process(np.zeros(flush, dtype=np.float32)),
        )
    )
    out = out[skip : skip + wanted]
    if len(out) < wanted:
        out = np.concatenate((out, np.zeros(wanted - len(out), dtype=np.float32)))
    return out.astype(np.float32, copy=False)


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
    """Expand mono to the output stream's channel count.

    **Copies the same waveform to every channel.**

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
