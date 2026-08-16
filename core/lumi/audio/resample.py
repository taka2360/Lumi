"""サンプルレート変換。**純粋関数**（デバイスに触らない）。

設計 → docs/architecture/audio.md §8

## なぜ要るのか

**16 kHz でストリームを開くことはできない。** WASAPI 共有モードはエンドポイントの
mix format のレート（普通 48 kHz）しか受け付けない（Phase 0 実測）。
一方 VAD と STT は 16 kHz を前提にしている。**その間を Core 内で埋める。**

## どこで変換するか

**VAD スレッドで行う。オーディオコールバックではしない。**
変換はメモリ確保を伴い、コールバックの締切（数 ms）を脅かす。
コールバックはデバイスのレートのままリングに書き、読む側が変換する。

## 品質について（正直に）

ここにあるのは **移動平均 + 線形補間**の簡易な実装である。
polyphase FIR（soxr / scipy）に比べれば折り返しの抑制は弱い。

**採ったのは「まず依存を増やさない」判断であって、品質が十分だという主張ではない。**
Step F の実測で STT 精度に影響が見えたら `soxr`（~300 KB）を足す。
そのときここだけを差し替えれば済むよう、**純粋関数1つに閉じてある。**
"""

from __future__ import annotations

import numpy as np

from lumi.audio.ring import Samples


def resample(x: Samples, src_rate: int, dst_rate: int) -> Samples:
    """`src_rate` の波形を `dst_rate` に変換する。

    ダウンサンプル時は**先に移動平均をかける**。かけないと、ナイキスト周波数より上が
    折り返して低域に化け、VAD が「声がある」と誤判定する材料になる。
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
    """インターリーブされた多チャンネルを mono に落とす。**平均する。**

    片方のチャンネルだけを採ると、そちらが無音のデバイス（実在する）で
    「聞こえているのに聞こえない」になる。
    """
    if channels <= 1:
        return x.astype(np.float32, copy=False)
    usable = (len(x) // channels) * channels
    if usable == 0:
        return np.zeros(0, dtype=np.float32)
    return x[:usable].reshape(-1, channels).mean(axis=1).astype(np.float32)


def to_interleaved(mono: Samples, channels: int) -> Samples:
    """mono を出力ストリームのチャンネル数に広げる。**全チャンネルに同じ波形を配る。**

    TTS の出力は mono だが、出力ストリームはデバイス既定のチャンネル数で開く
    （docs/architecture/audio.md §8）。片方だけに入れると片耳から聞こえる。
    """
    if channels <= 1:
        return mono.astype(np.float32, copy=False)
    return np.repeat(mono.astype(np.float32, copy=False), channels)


def pcm16_to_float32(data: bytes) -> Samples:
    """16bit PCM（TTS エンジンの出力）を float32 に。**-1.0〜1.0 に正規化する。**"""
    if not data:
        return np.zeros(0, dtype=np.float32)
    ints = np.frombuffer(data, dtype="<i2")
    return (ints.astype(np.float32) / 32768.0).astype(np.float32)
