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

## Why no lock is needed — every counter has exactly one writer

This is restricted to a single writer (the producer) x a single reader (the consumer),
and **each counter is owned by one side only**:

| Counter | Owner | Meaning |
|---|---|---|
| `_write` | producer | total samples written (monotonic) |
| `_discard` | producer | `clear()`'s mark: everything below it is given up on |
| `_read` | **consumer** | total samples consumed (monotonic) |
| `_dropped` | consumer | how much the producer overwrote before it could be read |

**The producer never advances `_read`, and the consumer never advances `_write`.** `int`
assignment and reads are atomic under the GIL, but `x += n` is not — it is load, add,
store. Two sides doing that to one counter lose updates, and a lost update to `_read` does
not fail loudly: it silently makes `available` wrong for the rest of the session, so the
consumer keeps handing out samples that were overwritten long ago. Overrun is therefore
detected by the consumer (`_advance`), and `clear()` publishes a mark instead of writing
`_read` — **a lost `clear()` is stale audio resuming after a barge-in.**

The samples themselves are still unsynchronised, so a producer that laps the consumer
mid-copy can tear a window. **That is detected after the copy and fails closed**: no window
at all beats a window stitched together from two different moments.

> **What this does not guarantee**: multiple writers / readers are not supported.
> To support more usage patterns, don't add a lock — **split into separate rings.**
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Samples = npt.NDArray[np.float32]


class RingBuffer:
    """Fixed length. **Drops the oldest on overflow** (prioritizes newer audio)."""

    __slots__ = ("_capacity", "_data", "_discard", "_dropped", "_read", "_write")

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        #: Pre-allocated up front. **Never allocated again inside the callback**
        self._data: Samples = np.zeros(capacity, dtype=np.float32)
        self._capacity = capacity
        # : Total write count / total read count (monotonically increasing; position derived via
        # modulo)
        self._write = 0
        self._read = 0
        #: `clear()`'s mark. **Producer-owned** — see the module docstring
        self._discard = 0
        # : **Count of dropped samples.** Never dropped silently — if this grows, the design or size
        # is wrong
        self._dropped = 0

    @property
    def available(self) -> int:
        """**Reads only.** Clamped to the capacity: whatever the producer wrote more than one
        lap ago is already gone, whether or not the consumer has noticed yet.
        """
        # Read the producer's discard mark before its write cursor. **If clear() lands between
        # these reads, the snapshot may be from different moments, but it must not report a
        # negative amount of audio.**
        discard = self._discard
        read = self._read
        write = self._write
        return max(0, min(write - max(read, discard), self._capacity))

    @property
    def dropped(self) -> int:
        return self._dropped

    def write(self, samples: Samples) -> None:
        """**Called from the audio callback.** No allocation, no locking, no exceptions.

        Touches the samples and `_write` only — **never the consumer's cursor.**
        """
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

    def _advance(self) -> int:
        """Moves the read cursor up to what is still readable, and returns it.

        **Only the consumer calls this**, which is what keeps `_read` single-owner. Two
        things move it: `clear()`'s mark, and the producer having lapped us — the latter is
        the drop, counted here rather than in `write`.
        """
        position = max(self._read, self._discard)
        behind = self._write - position - self._capacity
        if behind > 0:
            # The producer couldn't be kept up with. **The oldest is already overwritten**
            position += behind
            self._dropped += behind
        self._read = position
        return position

    def read(self, count: int) -> Samples | None:
        """Returns data if `count` samples are available.

        **`None` if not enough** (never a partial read).

        Allowing partial reads would let VAD's window arrive at an odd size and break inference.
        """
        if count <= 0:
            return None
        position = self._advance()
        if self._write - position < count:
            return None

        start = position % self._capacity
        end = start + count
        if end <= self._capacity:
            out = self._data[start:end].copy()
        else:
            out = np.concatenate((self._data[start:], self._data[: end - self._capacity]))
        self._read = position + count
        if self._write - position > self._capacity:
            # Lapped **while copying**: the window mixes two moments. **Fail closed** —
            # inference on a stitched-together window is worse than a missing window
            self._dropped += count
            return None
        return out.astype(np.float32, copy=False)

    def read_into(self, out: Samples) -> int:
        """**Called from the playback callback.** Fills any shortfall with 0 (silence).

        Returns the number of samples actually filled. **Zero-filling isn't an error condition**
        (TTS hasn't generated yet, or the utterance has ended).
        """
        count = len(out)
        position = self._advance()
        filled = min(count, self._write - position)
        if filled > 0:
            start = position % self._capacity
            end = start + filled
            if end <= self._capacity:
                out[:filled] = self._data[start:end]
            else:
                split = self._capacity - start
                out[:split] = self._data[start:]
                out[split:filled] = self._data[: end - self._capacity]
            self._read = position + filled
            if self._write - position > self._capacity:
                # Lapped while copying. **Silence beats a torn frame**
                self._dropped += filled
                out[:] = 0.0
                return 0
        if filled < count:
            out[filled:] = 0.0
        return filled

    def clear(self) -> None:
        """**Used when discarding playback for a barge-in.** Called from the producer side, so
        it publishes a mark rather than writing the consumer's cursor: a lost update here is
        stale audio resuming after the interruption.
        """
        self._discard = self._write
