"""マイク入力と VAD スレッド。

設計 → docs/architecture/audio.md §3

```
[audio callback]  リングに write するだけ        推論しない・確保しない・ロックしない
[VAD スレッド]    read → リサンプル → 推論 → 判定  ★ ここで音が止まる
[asyncio]         Activity 調停・STT             遅くてよい
```

**「音が止まる」は VAD スレッドが同期的に行い、Arbiter を経由しない。**
Activity の状態遷移ではないので Invariant 4 の対象外である
（docs/contracts/state-machines.md / ADR-018）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Final

import numpy as np

from lumi import logging as lumi_logging
from lumi.audio.devices import StreamPlan
from lumi.audio.resample import resample, to_mono
from lumi.audio.ring import RingBuffer, Samples
from lumi.audio.vad import (
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    SileroVad,
    SpeechSegmenter,
    VadEvent,
    VadParams,
)

log = lumi_logging.get_logger(__name__)

#: 入力リングの長さ〔Provisional〕。**溢れるより長く持つ**（VAD が一時的に遅れても失わない）
CAPTURE_RING_SECONDS: Final = 8.0

#: 最初のフレームが来るまでの猶予。**`open()` の成功では「聞けている」と言えない**
#: （無効化されたエンドポイントは開けるのにフレームが来ない → Phase 0 実測）
FIRST_FRAME_TIMEOUT_S: Final = 3.0

#: リングが空のときに VAD スレッドが眠る時間。フレーム長（32 ms）より十分短く
_IDLE_SLEEP_S: Final = 0.004

#: 発話区間の上限〔Provisional〕。**無限に貯めない**（喋り続ける環境音で RAM を食う）
MAX_SEGMENT_SECONDS: Final = 30.0


class CaptureUnavailable(RuntimeError):
    """開けない / フレームが来ない。**「聞いているつもりで無音を聞き続ける」を作らない。**"""


#: VAD からの通知。`audio` は SPEECH_ENDED のときだけ入る
VadListener = Callable[[VadEvent, Samples | None], None]


class MicrophoneCapture:
    """入力ストリーム。**コールバックはリングに書くだけ。**"""

    __slots__ = ("_first_frame", "_overflows", "_plan", "_ring", "_stream")

    def __init__(self, plan: StreamPlan) -> None:
        self._plan = plan
        capacity = int(plan.samplerate * CAPTURE_RING_SECONDS)
        self._ring = RingBuffer(capacity)
        self._first_frame = threading.Event()
        #: PortAudio が報告したオーバーフロー回数。**コールバックでは数えるだけ**
        #: （ログ I/O は締切違反）。読むのは VAD スレッドと停止時
        self._overflows = 0
        self._stream: Any = None

    @property
    def ring(self) -> RingBuffer:
        return self._ring

    @property
    def plan(self) -> StreamPlan:
        return self._plan

    def start(self) -> None:
        import sounddevice as sd

        try:
            self._stream = sd.InputStream(
                device=self._plan.device.index,
                samplerate=self._plan.samplerate,
                channels=self._plan.channels,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as error:
            raise CaptureUnavailable(f"入力を開けない: {error}") from error

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        """**リアルタイム制約下。** 推論しない・確保しない・ロックしない。

        `to_mono` は多チャンネルのときだけ確保する。単チャンネル（既定）では
        `reshape` のビューだけで済む。
        """
        del frames, time_info
        if status:
            # オーバーフロー等。**握りつぶさないが、ここでログを書かない**（I/O は締切違反）
            self._overflows += 1
        samples = np.asarray(indata, dtype=np.float32).reshape(-1)
        if self._plan.channels > 1:
            samples = to_mono(samples, self._plan.channels)
        self._ring.write(samples)
        if not self._first_frame.is_set():
            self._first_frame.set()

    def wait_until_open(self, timeout: float = FIRST_FRAME_TIMEOUT_S) -> None:
        """**最初のフレームが届いて初めて「聞けている」と扱う。**

        `open()` は無効化されたエンドポイントでも成功する（Phase 0 実測）。
        届かなければ**明示的に失敗させる。**
        """
        if not self._first_frame.wait(timeout):
            raise CaptureUnavailable(
                f"{self._plan.describe()}: {timeout} 秒待ってもフレームが来ない"
            )

    @property
    def overflows(self) -> int:
        return self._overflows

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._overflows or self._ring.dropped:
            # **黙って劣化していないかを、終わるときに必ず見る**
            log.warning("capture.lossy", overflows=self._overflows, dropped=self._ring.dropped)


class VadWorker:
    """**専用 OS スレッド。** asyncio を経由しない。

    ここが「すぐ黙る」を担当する。asyncio に渡すのは、その後の
    「Activity を止める」「STT にかける」という**遅くてよい仕事**だけ。
    """

    __slots__ = (
        "_is_playing",
        "_listener",
        "_mute_flag",
        "_pending",
        "_ring",
        "_segmenter",
        "_source_rate",
        "_stop",
        "_thread",
        "_vad",
    )

    def __init__(
        self,
        ring: RingBuffer,
        source_rate: int,
        mute_flag: threading.Event,
        listener: VadListener,
        *,
        vad: SileroVad | None = None,
        params: VadParams | None = None,
        is_playing: Callable[[], bool] | None = None,
    ) -> None:
        self._ring = ring
        self._source_rate = source_rate
        self._mute_flag = mute_flag
        self._listener = listener
        self._vad = vad
        self._segmenter = SpeechSegmenter(params)
        self._is_playing = is_playing or (lambda: False)
        self._pending: Samples = np.zeros(0, dtype=np.float32)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._vad is None:
            self._vad = SileroVad()
        self._thread = threading.Thread(target=self._loop, name="lumi-vad", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        # デバイスのレートで、16 kHz の1窓に相当する量ずつ読む
        chunk = max(1, WINDOW_SAMPLES * self._source_rate // SAMPLE_RATE)
        while not self._stop.is_set():
            raw = self._ring.read(chunk)
            if raw is None:
                time.sleep(_IDLE_SLEEP_S)
                continue
            read_at = time.perf_counter()
            converted = resample(raw, self._source_rate, SAMPLE_RATE)
            self._pending = (
                converted if len(self._pending) == 0 else np.concatenate((self._pending, converted))
            )
            while len(self._pending) >= WINDOW_SAMPLES:
                window = self._pending[:WINDOW_SAMPLES]
                self._pending = self._pending[WINDOW_SAMPLES:]
                self._process(window, read_at)

    def _process(self, window: Samples, read_at: float) -> None:
        assert self._vad is not None
        probability = self._vad.probability(window)
        events = self._segmenter.feed(probability, window, playing=self._is_playing())

        for event in events:
            if event is VadEvent.MUTE_REQUESTED:
                # ★ **critical path。** ここで音が止まる（同期・Arbiter を経由しない）
                self._mute_flag.set()
                log.info(
                    "vad.mute",
                    mute_latency_ms=round((time.perf_counter() - read_at) * 1000, 2),
                    probability=round(probability, 3),
                )
                self._listener(event, None)
            elif event is VadEvent.FALSE_TRIGGER:
                # **誤爆だった。戻す。** これがあるからミュート閾値を攻められる
                self._mute_flag.clear()
                log.info("vad.false_trigger")
                self._listener(event, None)
            elif event is VadEvent.SPEECH_ENDED:
                audio = self._segmenter.take()
                self._listener(event, _clamp(audio))
            else:
                self._listener(event, None)


def _clamp(audio: Samples) -> Samples:
    """**無限に長い区間を STT に渡さない。** 末尾を優先して切る。"""
    limit = int(SAMPLE_RATE * MAX_SEGMENT_SECONDS)
    if len(audio) <= limit:
        return audio
    log.warning("vad.segment_truncated", samples=len(audio), limit=limit)
    return audio[-limit:]
