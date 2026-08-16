"""Ring buffer. **The side touched from the audio callback never allocates or locks.**

Design → docs/architecture/audio.md §3

```
[audio callback]  Ring I/O and applying mute only     deadline: a few ms (hard)
[VAD thread]      inference / decisions                deadline: ~30 ms
[asyncio]         Activity arbitration / STT / response generation   can be slow
```

**Never call `np.concatenate` or `list.append` in the callback.** Memory allocation can
take an allocator lock and trigger GC. Either one, under real-time constraints, causes
buffer underruns (audible crackling), and **breaks even normal playback, let alone barge-in.**

## Why no lock is needed

This is restricted to a single writer (the audio callback) x a single reader (the VAD
thread). In CPython, `int` assignment and reads are atomic under the GIL, so
**consistency holds just from reading/writing the position counters.**

> **What this does not guarantee**: multiple writers / readers are not supported.
> To support more usage patterns, don't add a lock — **split into separate rings.**
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Samples = npt.NDArray[np.float32]


class RingBuffer:
    """Fixed length. **Drops the oldest on overflow** (prioritizes newer audio)."""

    __slots__ = ("_capacity", "_data", "_dropped", "_read", "_write")

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity は正の数")
        #: Pre-allocated up front. **Never allocated again inside the callback**
        self._data: Samples = np.zeros(capacity, dtype=np.float32)
        self._capacity = capacity
        # : Total write count / total read count (monotonically increasing; position derived via
        # modulo)
        self._write = 0
        self._read = 0
        # : **Count of dropped samples.** Never dropped silently — if this grows, the design or size
        # is wrong
        self._dropped = 0

    @property
    def available(self) -> int:
        return self._write - self._read

    @property
    def dropped(self) -> int:
        return self._dropped

    def write(self, samples: Samples) -> None:
        """**Called from the audio callback.** No allocation, no locking, no exceptions."""
        count = len(samples)
        if count == 0:
            return
        if count >= self._capacity:
            # A single write is larger than the ring. **Keep only the tail** (prioritize newer)
            samples = samples[-self._capacity :]
            count = self._capacity

        start = self._write % self._capacity
        end = start + count
        if end <= self._capacity:
            self._data[start:end] = samples
        else:
            split = self._capacity - start
            self._data[start:] = samples[:split]
            self._data[: end - self._capacity] = samples[split:]

        self._write += count
        overflow = self._write - self._read - self._capacity
        if overflow > 0:
            # The reader couldn't keep up. **Advance the read position, dropping the oldest**
            self._read += overflow
            self._dropped += overflow

    def read(self, count: int) -> Samples | None:
        """Returns data if `count` samples are available.

        **`None` if not enough** (never a partial read).

        Allowing partial reads would let VAD's window arrive at an odd size and break inference.
        """
        if count <= 0 or self.available < count:
            return None

        start = self._read % self._capacity
        end = start + count
        if end <= self._capacity:
            out = self._data[start:end].copy()
        else:
            out = np.concatenate((self._data[start:], self._data[: end - self._capacity]))
        self._read += count
        return out.astype(np.float32, copy=False)

    def read_into(self, out: Samples) -> int:
        """**Called from the playback callback.** Fills any shortfall with 0 (silence).

        Returns the number of samples actually filled. **Zero-filling isn't an error condition**
        (TTS hasn't generated yet, or the utterance has ended).
        """
        count = len(out)
        filled = min(count, self.available)
        if filled > 0:
            start = self._read % self._capacity
            end = start + filled
            if end <= self._capacity:
                out[:filled] = self._data[start:end]
            else:
                split = self._capacity - start
                out[:split] = self._data[start:]
                out[split:filled] = self._data[: end - self._capacity]
            self._read += filled
        if filled < count:
            out[filled:] = 0.0
        return filled

    def clear(self) -> None:
        """**Used when discarding playback for a barge-in.** Moves the read position up to the write
        position.
        """
        self._read = self._write
