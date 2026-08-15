"""スピーカー出力。**即時ミュートができることが実装要件。**

設計 → docs/architecture/audio.md §3, §6 / 決定 → ADR-020

> **TTS 再生の停止は `hard`。** バッファのミュートで即座に無音化できること
> （docs/contracts/state-machines.md「Cancellation 契約」）。

## Phase 0 の `play_wav` と何が違うか

Phase 0 は「鳴り終わるまで待つ」だけで、**止める手段が無かった**。
Phase 1 は常時開いたストリームにリング経由で書き込み、
**コールバックが `mute_flag` を見て 0 を出す。** ここが barge-in の出口。

## reference リング

**再生リングに書いたサンプルそのもの**を残す（ハードウェアから取らない）。
Lumi は再生する音を自分で作っているので、それが参照信号になる。
**ミュートした事実も含めて残す** — Phase 2 の AEC は「実際にスピーカーへ出た波形」を要る。
"""

from __future__ import annotations

import threading
from typing import Any, Final

import numpy as np

from lumi import logging as lumi_logging
from lumi.audio.devices import StreamPlan
from lumi.audio.ring import RingBuffer, Samples

log = lumi_logging.get_logger(__name__)

#: 再生リングの長さ〔Provisional〕。**先読み生成した数文を保持できる長さ**
PLAYBACK_RING_SECONDS: Final = 30.0
#: 参照信号のリング。AEC の遅延推定に必要な分（Phase 2）
REFERENCE_RING_SECONDS: Final = 4.0


class PlaybackUnavailable(RuntimeError):
    """出力を開けない。**黙って無音にしない。**"""


class SpeakerPlayback:
    """出力ストリーム + リング + `mute_flag`。"""

    __slots__ = ("_mute_flag", "_plan", "_reference", "_ring", "_stream", "_underruns")

    def __init__(self, plan: StreamPlan, *, mute_flag: threading.Event | None = None) -> None:
        self._plan = plan
        self._ring = RingBuffer(int(plan.samplerate * PLAYBACK_RING_SECONDS))
        self._reference = RingBuffer(int(plan.samplerate * REFERENCE_RING_SECONDS))
        #: **VAD スレッドと共有する。** これが立つと次のコールバックから無音になる
        self._mute_flag = mute_flag or threading.Event()
        self._underruns = 0
        self._stream: Any = None

    @property
    def mute_flag(self) -> threading.Event:
        return self._mute_flag

    @property
    def plan(self) -> StreamPlan:
        return self._plan

    @property
    def reference(self) -> RingBuffer:
        """Phase 2 の AEC が読む。**Phase 1 では書くだけ。**"""
        return self._reference

    def start(self) -> None:
        import sounddevice as sd

        try:
            self._stream = sd.OutputStream(
                device=self._plan.device.index,
                samplerate=self._plan.samplerate,
                channels=self._plan.channels,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as error:
            raise PlaybackUnavailable(f"出力を開けない: {error}") from error

    def _callback(self, outdata: Any, frames: int, time_info: Any, status: Any) -> None:
        """**リアルタイム制約下。** 見るのは `mute_flag` とリングだけ。"""
        del time_info
        if status:
            self._underruns += 1

        view = np.asarray(outdata, dtype=np.float32).reshape(-1)
        if self._mute_flag.is_set():
            # ★ **ここで音が止まる。** 生成中のものを待たない
            view[:] = 0.0
        else:
            self._ring.read_into(view)

        # **実際に出した分**を参照信号として残す（ミュートした 0 も含めて）
        self._reference.write(view)
        del frames

    def write(self, samples: Samples) -> None:
        """再生キューに足す。**デバイスのレートに揃えてから渡すこと。**"""
        self._ring.write(samples)

    def is_active(self) -> bool:
        """再生中か。**EchoGuard L1 の閾値ブーストはこれで決まる。**"""
        return not self._mute_flag.is_set() and self._ring.available > 0

    def mute(self) -> None:
        """**barge-in の出口。** 同期。次のコールバックから無音になる。"""
        self._mute_flag.set()

    def unmute(self) -> None:
        self._mute_flag.clear()

    def clear(self) -> None:
        """**再生予定を捨てる。** 中断したあと、古い音が再開しないように。"""
        self._ring.clear()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._underruns:
            log.warning("playback.underruns", count=self._underruns)


# ── Phase 0 の遺物 ────────────────────────────────────────
# **`greeting.py` が消える Step E で一緒に消す。** 止める手段が無いので、
# Phase 1 の会話経路では使わない（barge-in が成立しない）。


class PlaybackError(RuntimeError):
    """鳴らせなかった。**理由を持つ**（黙って無音にしない）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _play_blocking(data: bytes) -> None:
    """PortAudio に渡して鳴り終わるまで待つ。**専用スレッドで呼ぶこと。**"""
    from lumi.audio.wav import WavError, decode_wav

    try:
        import sounddevice as sd
    except OSError as error:
        raise PlaybackError("audio_backend_unavailable", str(error)) from error

    try:
        wav = decode_wav(data)
    except WavError as error:
        raise PlaybackError("wav_malformed", str(error)) from error

    if wav.sample_width != 2:
        raise PlaybackError("unsupported_sample_width", f"{wav.sample_width * 8}bit")

    try:
        with sd.RawOutputStream(
            samplerate=wav.sample_rate, channels=wav.channels, dtype="int16"
        ) as stream:
            stream.write(wav.frames)
    except Exception as error:
        raise PlaybackError("playback_failed", str(error)) from error


async def play_wav(data: bytes) -> None:
    """WAV を鳴らし終わるまで待つ。**止められない**（Phase 0 の範囲）。"""
    import asyncio

    await asyncio.to_thread(_play_blocking, data)
