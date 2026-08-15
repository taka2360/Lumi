"""リングバッファ。**オーディオコールバックから触る側は、確保もロックもしない。**

設計 → docs/architecture/audio.md §3

```
[audio callback]  リング入出力とミュート適用のみ    締切: 数 ms（ハード）
[VAD スレッド]    推論・判定                        締切: 〜30 ms
[asyncio]         Activity 調停・STT・応答生成       遅くてよい
```

**コールバックで `np.concatenate` も `list.append` もしない。** メモリ確保は
アロケータのロックを取りうるし、GC を誘発する。どちらもリアルタイム制約下では
バッファアンダーラン（プチプチ音）になり、**barge-in どころか通常の再生が壊れる。**

## なぜロックが要らないか

単一 writer（オーディオコールバック）× 単一 reader（VAD スレッド）に限定してある。
CPython では `int` の代入と読み出しが GIL の下でアトミックなので、
**位置カウンタの読み書きだけで整合が取れる。**

> **保証しないこと**: 複数の writer / reader は想定していない。
> 使い方を増やすときはロックを足すのではなく、**リングを分ける。**
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

Samples = npt.NDArray[np.float32]


class RingBuffer:
    """固定長。**溢れたら古いものから捨てる**（新しい音を優先する）。"""

    __slots__ = ("_capacity", "_data", "_dropped", "_read", "_write")

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity は正の数")
        #: 事前に確保しておく。**コールバックの中では二度と確保しない**
        self._data: Samples = np.zeros(capacity, dtype=np.float32)
        self._capacity = capacity
        #: 総書き込み数 / 総読み出し数（単調増加。剰余で位置を出す）
        self._write = 0
        self._read = 0
        #: **捨てた数。** 黙って捨てない — これが増えていたら設計かサイズが間違っている
        self._dropped = 0

    @property
    def available(self) -> int:
        return self._write - self._read

    @property
    def dropped(self) -> int:
        return self._dropped

    def write(self, samples: Samples) -> None:
        """**オーディオコールバックから呼ぶ。** 確保しない・ロックしない・例外を投げない。"""
        count = len(samples)
        if count == 0:
            return
        if count >= self._capacity:
            # 1回の書き込みがリングより大きい。**末尾だけ残す**（最新を優先）
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
            # reader が追いつけなかった。**読み位置を進めて、古い方を捨てる**
            self._read += overflow
            self._dropped += overflow

    def read(self, count: int) -> Samples | None:
        """`count` サンプル揃っていれば返す。**足りなければ `None`**（部分読みをしない）。

        部分読みを許すと、VAD の窓が半端なサイズで来て推論が壊れる。
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
        """**再生側のコールバックから呼ぶ。** 足りない分は 0 で埋める（無音）。

        戻り値は実際に埋めたサンプル数。**0 埋めは異常ではない**
        （TTS がまだ生成していない、あるいは発話が終わった）。
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
        """**barge-in で再生を捨てるときに使う。** 書き込み位置に読み位置を合わせる。"""
        self._read = self._write
