"""ドリフト推定（純粋関数）。合成した系列で検証する。

設計 → docs/decisions/ADR-020-split-audio-streams.md
"""

from __future__ import annotations

import random

from lumi.audio.drift import MIN_SAMPLES, estimate_drift, relative_ppm

BLOCK_S = 512 / 48000


def series(
    ppm: float, count: int = 4000, jitter_ms: float = 0.0, seed: int = 7
) -> list[tuple[float, float]]:
    """実時間に対して `ppm` だけ速いストリームの系列を作る。

    `jitter_ms` はコールバックの先読み量の揺れ（**実測では 3 ms 程度**）。
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
        """**残差が ppm の信頼度を伝える。**

        コールバックの先読み量の揺れ（実測 3 ms 程度）は傾きを不確かにする。
        残差を返すのは、**その ppm をどこまで信じてよいかを呼び出し側が判断できるようにするため**。
        """
        estimate = estimate_drift(series(50.0, jitter_ms=3.0))
        assert estimate is not None
        assert estimate.residual_ms > 1.0
        assert abs(estimate.ppm - 50.0) < 20.0

    def test_measuring_longer_resolves_smaller_drift(self) -> None:
        """**分解能は測定時間で決まる。**

        同じ揺れでも長く測れば傾きは締まる。「2 ppm だった」と言えるかどうかは
        測定時間次第であり、短い測定で小さい ppm を主張してはいけない。
        """
        short = estimate_drift(series(50.0, jitter_ms=3.0, count=2000))
        long = estimate_drift(series(50.0, jitter_ms=3.0, count=20000))
        assert short is not None and long is not None
        assert abs(long.ppm - 50.0) < abs(short.ppm - 50.0)
        assert abs(long.ppm - 50.0) < 3.0

    def test_startup_transient_is_excluded(self) -> None:
        """開始直後のバッファ充填を含めると傾きが立つ。**後半だけを使う。**"""
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
        """AEC を壊すのは実時間とのずれではなく、**入出力どうしのずれ**。"""
        capture = estimate_drift(series(100.0))
        playback = estimate_drift(series(98.0))
        assert capture is not None and playback is not None
        assert abs(relative_ppm(capture, playback) - 2.0) < 0.5

    def test_ms_per_minute(self) -> None:
        estimate = estimate_drift(series(1000.0))
        assert estimate is not None
        assert abs(estimate.ms_per_minute() - 60.0) < 1.0
