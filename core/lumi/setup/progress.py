"""How often a fetch says how far it has got. **Rarely enough not to flood the wire.**

Design → docs/architecture/setup.md §4

A download reports every chunk. Broadcasting each one would put thousands of setup-state
snapshots on a socket whose other end redraws on every message, for a bar that moves in
pixels. One percent is the resolution the bar actually has.

The two rules here look similar and are not. `throttle` gates a fraction someone else
computed. `LayerProgress` computes the fraction, because Ollama does not report one:
it reports bytes per layer, and the layers arrive one after another.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

#: The smallest move worth sending. **One percent of the bar** — below that the Stage
#: redraws the same picture.
PROGRESS_STEP: Final = 0.01


def throttle(report: Callable[[float], Awaitable[None]]) -> Callable[[float], Awaitable[None]]:
    """Passes a fraction through at most every `PROGRESS_STEP`. **1.0 always gets through**
    — the last update is the one that says the bar is finished, and dropping it leaves it
    stuck near the end forever.
    """
    last_sent = -1.0

    async def throttled(fraction: float) -> None:
        nonlocal last_sent
        if fraction - last_sent < PROGRESS_STEP and fraction < 1.0:
            return
        last_sent = fraction
        await report(fraction)

    return throttled


class LayerProgress:
    """Bytes per layer, into one fraction. **Returns `None` for an update not worth sending.**

    Ollama pulls a model as a series of layers and reports `(completed, total)` for
    whichever it is on. Feeding those straight to a progress bar produces two failures
    that both look like a hang:

    - a small metadata layer finishes, so the bar **reaches 100% and then freezes** while
      the multi-gigabyte layer downloads behind it
    - a new layer with the same size as the last one restarts its byte count, so the
      fraction drops — and a gate comparing against the previous layer's high-water mark
      **never lets anything through again**

    So a bigger total means a new layer worth following (reset), a smaller one means a
    layer already left behind (ignore), and a backwards byte count means the same-sized
    layer started over (let it through).
    """

    __slots__ = ("_completed", "_sent", "_total")

    def __init__(self) -> None:
        self._total = 0
        self._completed = -1
        self._sent = -1.0

    def update(self, completed: int, total: int) -> float | None:
        """The fraction to show, or `None` to send nothing."""
        if total < self._total:
            return None
        if total > self._total:
            self._total = total
            self._sent = -1.0
            self._completed = -1
        if completed < self._completed:
            self._sent = -1.0
        self._completed = completed
        fraction = min(1.0, max(0.0, completed / total))
        if fraction - self._sent < PROGRESS_STEP and fraction < 1.0:
            return None
        self._sent = fraction
        return fraction
