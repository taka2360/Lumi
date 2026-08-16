"""Drift estimation (pure functions). Verified against synthetic sequences.

Design → docs/decisions/ADR-020-split-audio-streams.md
"""

from __future__ import annotations

import random

from lumi.audio.drift import MIN_SAMPLES, estimate_drift, relative_ppm

BLOCK_S = 512 / 48000


def series(
    ppm: float, count: int = 4000, jitter_ms: float = 0.0, seed: int = 7
) -> list[tuple[float, float]]:
    """Builds a stream sequence running `ppm` faster than wall-clock time.

    `jitter_ms` is the jitter in the callback's look-ahead amount (**observed at
    around 3 ms**).
    """
    rng = random.Random(seed)
    out = []
    for index in range(count):
        wall = index * BLOCK_S
        stream = wall * (1.0 + ppm / 1e6)
        if jitter_ms:
            stream += rng.uniform(-jitter_ms, jitter_ms) / 1e3
        out.append((wall, stream))
    return out


class TestEstimate:
    def test_recovers_a_known_drift(self) -> None:
        estimate = estimate_drift(series(120.0))
        assert estimate is not None
        assert abs(estimate.ppm - 120.0) < 1.0

    def test_zero_drift_reads_as_zero(self) -> None:
        estimate = estimate_drift(series(0.0))
        assert estimate is not None
        assert abs(estimate.ppm) < 1.0

    def test_jitter_shows_up_in_the_residual(self) -> None:
        """**The residual conveys how much to trust the ppm.**

        Jitter in the callback's look-ahead amount (observed at around 3 ms) makes
        the slope uncertain. The residual is returned so **the caller can judge how
        far to trust that ppm value**.
        """
        estimate = estimate_drift(series(50.0, jitter_ms=3.0))
        assert estimate is not None
        assert estimate.residual_ms > 1.0
        assert abs(estimate.ppm - 50.0) < 20.0

    def test_measuring_longer_resolves_smaller_drift(self) -> None:
        """**Resolution is determined by how long the measurement runs.**

        Given the same jitter, measuring longer tightens the slope. Whether "it was
        2 ppm" can be claimed depends on the measurement duration, and a short
        measurement should never claim a small ppm value.
        """
        short = estimate_drift(series(50.0, jitter_ms=3.0, count=2000))
        long = estimate_drift(series(50.0, jitter_ms=3.0, count=20000))
        assert short is not None and long is not None
        assert abs(long.ppm - 50.0) < abs(short.ppm - 50.0)
        assert abs(long.ppm - 50.0) < 3.0

    def test_startup_transient_is_excluded(self) -> None:
        """Including buffer-filling right after start produces a spurious slope. **Only the second half is used.**"""
        samples = series(0.0)
        warmed = [(wall, stream + min(wall, 0.5) * 0.2) for wall, stream in samples]
        estimate = estimate_drift(warmed)
        assert estimate is not None
        assert abs(estimate.ppm) < 1.0


class TestRefusesToGuess:
    def test_too_few_samples_returns_none(self) -> None:
        assert estimate_drift(series(100.0, count=MIN_SAMPLES - 1)) is None

    def test_no_elapsed_time_returns_none(self) -> None:
        assert estimate_drift([(1.0, 1.0)] * (MIN_SAMPLES * 2)) is None


class TestRelative:
    def test_relative_is_the_difference(self) -> None:
        """What breaks AEC isn't drift from wall-clock time — it's **drift between input and output**."""
        capture = estimate_drift(series(100.0))
        playback = estimate_drift(series(98.0))
        assert capture is not None and playback is not None
        assert abs(relative_ppm(capture, playback) - 2.0) < 0.5

    def test_ms_per_minute(self) -> None:
        estimate = estimate_drift(series(1000.0))
        assert estimate is not None
        assert abs(estimate.ms_per_minute() - 60.0) < 1.0
