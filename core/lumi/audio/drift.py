"""ストリームのクロックが実時間からどれだけずれているかを推定する。**純粋関数**。

設計 → [ADR-020](../../../docs/decisions/ADR-020-split-audio-streams.md)

入出力を別ストリームで開く以上、**両者が同じ速さで進んでいることは保証されない。**
その差（相対ドリフト）が Phase 2 の AEC の再整合頻度を決めるので、測れるようにしておく。

オーディオコールバックは「先読み」で呼ばれ、その先読み量は数 ms 揺れる。
2点の差で傾きを出すとこの揺れがそのまま ppm に化けるため、**最小二乗を当て、残差も返す。**
残差が大きいまま出た ppm は信用してはいけない。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

#: 傾きを当てる最小のサンプル数。これを下回るなら推定しない（**当てずっぽうを返さない**）。
MIN_SAMPLES = 64


@dataclass(frozen=True, slots=True)
class DriftEstimate:
    """実時間に対するストリーム時間の進み方。"""

    ppm: float
    """百万分率。+100 なら実時間より 100 ppm 速い。"""

    residual_ms: float
    """直線からの残差（標準偏差）。**これが大きいなら ppm を信用しない。**"""

    samples: int

    def ms_per_minute(self) -> float:
        return abs(self.ppm) * 60 / 1e3


def estimate_drift(samples: Sequence[tuple[float, float]]) -> DriftEstimate | None:
    """`(実時間[秒], ストリーム時間[秒])` の列から進み方を推定する。

    **後半だけを使う。** 開始直後はバッファの充填で傾きが立つため、そこを含めると過大に出る。
    サンプルが足りなければ `None`（**推定できないことを推定値で埋めない**）。
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
    """入力と出力のクロックの差。**AEC を壊すのはこの値。**"""
    return capture.ppm - playback.ppm
