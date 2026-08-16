"""VAD — **barge-in の critical path。**

設計 → docs/architecture/audio.md §3, §5 / 決定 → ADR-003

## ミュート判定と発話区間確定を分ける

**この2つは別の閾値で別々に行う。**

| | ミュート判定 | 発話区間の確定 |
|---|---|---|
| 目的 | **すぐ黙る** | STT に渡す区間を切る |
| 閾値 | `mute_threshold`（高め・即時） | `speech_threshold` + ヒステリシス |
| 最小継続 | **無し**（1フレームで発火） | `min_speech_duration_ms` |
| 誤爆時 | **戻して再生を再開** | 区間を破棄 |
| 到達先 | `mute_flag`（同期） | asyncio → Arbiter |

同じにすると、`min_speech_duration_ms = 250` を待つ間ずっと Lumi が喋り続ける。
**誤爆から復帰できる構造にしておくことで、ミュート閾値をかなり攻められる。**
「一瞬止まってすぐ戻る」は「300 ms 被り続ける」より遥かに体感が良い。

## EchoGuard L1 — 「抑制」しない

再生中は `mute_threshold` を**上げるだけ**。入力を止めない。
AIRI の「発話中は音声入力を抑制」は自己ループを防ぐが、**barge-in を原理的に不可能にする。**
大きな声なら必ず割り込める、が Lumi の側の要件である。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import numpy as np

from lumi.audio.ring import Samples

#: VAD が前提とするレート。ストリームはこのレートで開けないので Core 内で変換する
SAMPLE_RATE: Final = 16_000
#: Silero v5 以降の窓（512 サンプル = 32 ms）
WINDOW_SAMPLES: Final = 512
#: 前フレームの末尾を文脈として結合する（モデルの入力は 576）
CONTEXT_SAMPLES: Final = 64
FRAME_MS: Final = WINDOW_SAMPLES * 1000 // SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class VadParams:
    """〔Provisional〕AIRI の実運用値を出発点にする（docs/architecture/audio.md §5）。"""

    #: **ミュート判定（即時）。誤爆を許容する**
    mute_threshold: float = 0.5
    #: 発話区間の開始。**ミュートより低い**（語頭を取りこぼさない）
    speech_threshold: float = 0.3
    #: 発話区間の終了（ヒステリシス）
    exit_threshold: float = 0.1
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 400
    #: 語頭を切らないためのプリロール
    speech_pad_ms: int = 80
    #: この時間内に区間が確定しなければ**ミュートを戻す**
    false_trigger_ms: int = 300
    #: **EchoGuard L1。** 再生中に閾値を何倍にするか。**抑制ではない**
    playback_boost: float = 1.4

    def mute_threshold_for(self, *, playing: bool) -> float:
        return self.mute_threshold * (self.playback_boost if playing else 1.0)


class VadEvent(StrEnum):
    #: **今すぐ黙れ。** VAD スレッドが同期的に `mute_flag` を立てる（Arbiter を経由しない）
    MUTE_REQUESTED = "mute_requested"
    #: 誤爆だった。**再生を戻す**
    FALSE_TRIGGER = "false_trigger"
    #: 発話区間が確定した → asyncio → `arbiter.interrupt()`
    SPEECH_STARTED = "speech_started"
    #: 発話が終わった → asyncio → STT
    SPEECH_ENDED = "speech_ended"


class SpeechSegmenter:
    """**純粋なロジック。** モデルもデバイスも触らない（確率の列を食べてイベントを吐く）。

    ここが純粋であることが、barge-in をテストできることの土台になっている。
    """

    __slots__ = (
        "_candidate",
        "_confirmed_since_mute",
        "_muted_frames",
        "_params",
        "_preroll",
        "_segment",
        "_silence_frames",
        "_speech_frames",
        "in_speech",
        "muted",
    )

    def __init__(self, params: VadParams | None = None) -> None:
        self._params = params or VadParams()
        preroll_frames = max(1, self._params.speech_pad_ms // FRAME_MS)
        #: 語頭のためのプリロール。**発話が始まる「前」のフレーム**
        self._preroll: deque[Samples] = deque(maxlen=preroll_frames)
        #: 発話が始まってから**区間が確定するまで**のフレーム。
        #: これを捨てると `min_speech_duration_ms`（250 ms）分まるごと語頭が消える
        self._candidate: list[Samples] = []
        self._segment: list[Samples] = []
        self._speech_frames = 0
        self._silence_frames = 0
        self._muted_frames = 0
        #: このミュート以降に発話区間が確定したか。**確定したなら誤爆ではない**
        self._confirmed_since_mute = False
        self.muted = False
        self.in_speech = False

    @property
    def params(self) -> VadParams:
        return self._params

    def feed(self, probability: float, frame: Samples, *, playing: bool) -> tuple[VadEvent, ...]:
        """1フレーム分を食べて、起きたことを返す。"""
        events: list[VadEvent] = []
        params = self._params

        # ── (a) ミュート判定 — 即時・**最小継続を適用しない** ──
        if playing and not self.muted and probability > params.mute_threshold_for(playing=True):
            self.muted = True
            self._muted_frames = 0
            self._confirmed_since_mute = False
            events.append(VadEvent.MUTE_REQUESTED)
        elif self.muted:
            self._muted_frames += 1

        # ── (b) 発話区間の確定 — ヒステリシス + 最小継続 ──
        if not self.in_speech:
            if probability >= params.speech_threshold:
                # **確定していなくても貯める。** 捨てると、確定までの 250 ms が丸ごと消える
                self._speech_frames += 1
                self._candidate.append(frame)
            else:
                # 声ではなかった。候補をプリロールに流して捨てる
                self._speech_frames = 0
                self._preroll.extend(self._candidate)
                self._candidate.clear()
                self._preroll.append(frame)

            if self._speech_frames * FRAME_MS >= params.min_speech_duration_ms:
                self.in_speech = True
                self._silence_frames = 0
                # **プリロール（発話の前）+ 候補（確定まで）** を先頭に付ける
                self._segment = [*self._preroll, *self._candidate]
                self._preroll.clear()
                self._candidate.clear()
                # **このミュートは正しかった。** 以降 (c) の対象にしない
                self._confirmed_since_mute = True
                events.append(VadEvent.SPEECH_STARTED)
        else:
            self._segment.append(frame)
            if probability < params.exit_threshold:
                self._silence_frames += 1
            else:
                self._silence_frames = 0
            if self._silence_frames * FRAME_MS >= params.min_silence_duration_ms:
                self.in_speech = False
                self._speech_frames = 0
                events.append(VadEvent.SPEECH_ENDED)

        # ── (c) 誤爆からの復帰 ──
        # **確定した発話を「誤爆」と呼ばない。** 区間が終われば (b) は in_speech を下ろすので、
        # `_confirmed_since_mute` が無いと、正しく遮った発話の直後に必ずここが発火する。
        # そうなると呼び出し側は「遮ったのは間違いだった」と受け取り、
        # **中断したはずの再生を戻してしまう。**
        if (
            self.muted
            and not self.in_speech
            and not self._confirmed_since_mute
            and self._muted_frames * FRAME_MS >= params.false_trigger_ms
        ):
            self.muted = False
            self._muted_frames = 0
            events.append(VadEvent.FALSE_TRIGGER)

        return tuple(events)

    def take(self) -> Samples:
        """確定した区間を取り出す。**取り出したら空にする。**"""
        if not self._segment:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._segment).astype(np.float32)
        self._segment = []
        return audio

    def unmute(self) -> None:
        """外からミュート状態を解除する。**確定した発話の後はこれが唯一の戻し方。**

        (c) は誤爆にしか効かないので、**遮ってから次に喋り出すまでの間に
        呼び出し側が明示的に戻す**（`VadWorker.resume` → `AudioIO.resume_listening`）。
        呼ばないと `muted` が立ったままになり、**barge-in が1回しか効かない。**
        """
        self.muted = False
        self._muted_frames = 0
        self._confirmed_since_mute = False


class VadModelUnavailable(RuntimeError):
    """モデルが見つからない / 読めない。**黙って「声が無い」ことにしない。**"""


def default_model_path() -> Path:
    """**faster-whisper が同梱している Silero VAD を使う**（→ docs/licensing.md §4.6）。

    別途取得しないのは、PyPI の `silero-vad` が **torch に依存する**ため（R1）。

    **保証しないこと**: これは faster-whisper のパッケージ内部構造への依存であり、
    更新でパスが変われば壊れる。**壊れたら明示的に失敗する**（fail-closed）ので、
    黙って VAD が効かない状態にはならない。
    """
    try:
        import faster_whisper
    except ImportError as error:  # pragma: no cover
        raise VadModelUnavailable(f"faster_whisper が無い: {error}") from error

    path = Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"
    if not path.is_file():
        raise VadModelUnavailable(f"Silero VAD の ONNX が見つからない: {path}")
    return path


class SileroVad:
    """Silero VAD (ONNX Runtime, CPU)。**VRAM を使わない。**

    **オーディオコールバックから呼ばない。** ONNX 推論は GIL を取り、
    アロケータとスレッドプールの挙動で数十 ms のスパイクを出しうる。
    それは p99 に直撃し、通常の再生まで壊す（docs/architecture/audio.md §3）。
    """

    __slots__ = ("_c", "_context", "_h", "_session")

    def __init__(self, model_path: Path | None = None) -> None:
        import onnxruntime  # 遅延 import。起動を重くしない

        options = onnxruntime.SessionOptions()
        # **CPU 1スレッドに固定する。** VAD は 32 ms ごとの小さな推論であり、
        # スレッドを増やすと LLM / TTS と CPU を奪い合うだけで速くならない
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        try:
            self._session = onnxruntime.InferenceSession(
                str(model_path or default_model_path()),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise VadModelUnavailable(str(error)) from error

        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def probability(self, frame: Samples) -> float:
        """512 サンプル（32 ms / 16 kHz）を食べて、発話らしさを返す。"""
        if len(frame) != WINDOW_SAMPLES:
            raise ValueError(f"{WINDOW_SAMPLES} サンプルが必要（来たのは {len(frame)}）")

        # v5 以降は「前フレームの末尾 64 サンプル + 今回の 512」を入力にする
        window = np.concatenate((self._context, frame)).reshape(1, -1).astype(np.float32)
        outputs: list[Any] = self._session.run(None, {"input": window, "h": self._h, "c": self._c})
        probability, self._h, self._c = outputs[0], outputs[1], outputs[2]
        self._context = frame[-CONTEXT_SAMPLES:].astype(np.float32)
        return float(np.asarray(probability).reshape(-1)[0])

    def reset(self) -> None:
        """セッションを跨ぐときに状態を捨てる。**LSTM の状態が残ると誤判定する。**"""
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
