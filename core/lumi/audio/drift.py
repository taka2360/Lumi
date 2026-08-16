"""Estimates how far a stream's clock drifts from wall-clock time. **Pure functions.**

Design → [ADR-020](../../../docs/decisions/ADR-020-split-audio-streams.md)

Since input and output are opened as separate streams, **there's no guarantee they
advance at the same rate.** That difference (relative drift) determines how often
Phase 2's AEC needs to re-align, so it needs to be measurable.

The audio callback is invoked with "look-ahead," and the amount of look-ahead jitters
by a few ms. Computing a slope from just two points would turn that jitter directly
into ppm error, so **a least-squares fit is used, and the residual is also returned.**
A ppm value with a large residual should not be trusted.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

# : Minimum sample count needed to fit a slope. Below this, no estimate is made (**never return a
# guess**).
MIN_SAMPLES = 64


@dataclass(frozen=True, slots=True)
class DriftEstimate:
    """How stream time advances relative to wall-clock time."""

    ppm: float
    """Parts per million. +100 means 100 ppm faster than wall-clock time."""

    residual_ms: float
    """Residual from the fitted line (standard deviation). **Don't trust ppm if this is large.**"""

    samples: int

    def ms_per_minute(self) -> float:
        return drift_ms(self.ppm, 60.0)


def drift_ms(ppm: float, seconds: float) -> float:
    """How many ms a `ppm` drift accumulates over `seconds` seconds. **Sign is discarded**
    (only magnitude matters).

    The ppm → ms conversion **lives in this one place.** Writing it out at each call
    site would inevitably diverge between relative drift (`relative_ppm`'s return value)
    and absolute drift.
    """
    return abs(ppm) * seconds / 1e3


def estimate_drift(samples: Sequence[tuple[float, float]]) -> DriftEstimate | None:
    """Estimate the rate from a sequence of `(wall time [s], stream time [s])`.

    **Only the second half is used.** Right after start, buffer filling produces a
    spurious slope, and including it would overestimate. Returns `None` if there aren't
    enough samples (**never fill "can't estimate" with a guess**).
    """
    if len(samples) < MIN_SAMPLES:
        return None
    half = samples[len(samples) // 2 :]
    xs = [x for x, _ in half]
    ys = [y for _, y in half]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0.0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    residuals = [(y - (slope * x + intercept)) * 1e3 for x, y in zip(xs, ys, strict=True)]
    return DriftEstimate(
        ppm=(slope - 1.0) * 1e6,
        residual_ms=statistics.pstdev(residuals),
        samples=len(half),
    )


def relative_ppm(capture: DriftEstimate, playback: DriftEstimate) -> float:
    """The difference between input and output clocks. **This is the value that breaks AEC.**"""
    return capture.ppm - playback.ppm
