"""VAD と barge-in の判定。**docs/architecture/audio.md のテスト表 2 / 4b / 4c / 5 / 6 / 9 / 12。**

`SpeechSegmenter` が純粋なロジックなので、**デバイスもモデルも無しで barge-in を試験できる。**
"""

from __future__ import annotations

import ast
import statistics
import time
from pathlib import Path

import numpy as np
import pytest

from lumi.audio.vad import (
    FRAME_MS,
    WINDOW_SAMPLES,
    SileroVad,
    SpeechSegmenter,
    VadEvent,
    VadParams,
)

FRAME = np.zeros(WINDOW_SAMPLES, dtype=np.float32)


def feed(
    segmenter: SpeechSegmenter, probabilities: list[float], *, playing: bool = False
) -> list[VadEvent]:
    events: list[VadEvent] = []
    for probability in probabilities:
        events.extend(segmenter.feed(probability, FRAME, playing=playing))
    return events


def frames_for(ms: int) -> int:
    return ms // FRAME_MS + 1


# ── ミュート判定（即時）────────────────────────────────────


def test_mute_fires_on_a_single_frame() -> None:
    """**`min_speech_duration_ms` をミュート判定に適用しない**（表4b）。

    適用すると 250 ms のあいだ Lumi が喋り続ける。**それが最も体感を壊す。**
    """
    segmenter = SpeechSegmenter()
    events = feed(segmenter, [0.9], playing=True)
    assert events == [VadEvent.MUTE_REQUESTED]


def test_mute_does_not_fire_when_nothing_is_playing() -> None:
    """喋っていないのにミュートしても意味がない（区間確定だけが進む）。"""
    segmenter = SpeechSegmenter()
    assert feed(segmenter, [0.9], playing=False) == []


def test_playback_boost_ignores_a_quiet_room(  # 表5
) -> None:
    """**EchoGuard L1。** 再生中は閾値が上がる（0.5 → 0.7）。"""
    segmenter = SpeechSegmenter()
    # 0.6 は非再生時なら超えるが、再生中は超えない
    assert feed(segmenter, [0.6], playing=True) == []


def test_a_loud_voice_always_interrupts(  # 表6
) -> None:
    """**抑制していないことの確認。** 大きな声なら再生中でも必ず割り込める。"""
    segmenter = SpeechSegmenter()
    assert VadEvent.MUTE_REQUESTED in feed(segmenter, [0.95], playing=True)


def test_a_false_trigger_restores_playback() -> None:
    """**誤爆から戻れる。** これがあるからミュート閾値を攻められる（表4c）。

    「一瞬止まってすぐ戻る」は「300 ms 被り続ける」より遥かに体感が良い。
    """
    segmenter = SpeechSegmenter()
    feed(segmenter, [0.9], playing=True)
    assert segmenter.muted

    quiet = [0.0] * frames_for(VadParams().false_trigger_ms)
    events = feed(segmenter, quiet, playing=True)

    assert VadEvent.FALSE_TRIGGER in events
    assert not segmenter.muted


def test_a_real_utterance_does_not_get_unmuted() -> None:
    """区間が確定していれば、誤爆ではないのでミュートを戻さない。"""
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    loud = [0.9] * frames_for(params.min_speech_duration_ms)
    events = feed(segmenter, loud, playing=True)

    assert VadEvent.MUTE_REQUESTED in events
    assert VadEvent.SPEECH_STARTED in events
    assert VadEvent.FALSE_TRIGGER not in events
    assert segmenter.muted


# ── 発話区間の確定 ──────────────────────────────────────────


def test_speech_needs_a_minimum_duration() -> None:
    """**単発のノイズで STT を起こさない。**"""
    segmenter = SpeechSegmenter()
    assert feed(segmenter, [0.9, 0.0, 0.9]) == []


def test_a_single_utterance_starts_and_ends() -> None:
    params = VadParams()
    segmenter = SpeechSegmenter(params)

    events = feed(segmenter, [0.9] * frames_for(params.min_speech_duration_ms))
    assert VadEvent.SPEECH_STARTED in events

    events = feed(segmenter, [0.0] * frames_for(params.min_silence_duration_ms))
    assert VadEvent.SPEECH_ENDED in events
    assert not segmenter.in_speech


def test_a_long_leading_silence_is_ignored() -> None:
    """表2。先頭の無音がいくら長くても区間は始まらない。"""
    segmenter = SpeechSegmenter()
    assert feed(segmenter, [0.0] * 200) == []


def test_two_utterances_produce_two_segments() -> None:
    """表2。連続2発話。"""
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    speech = [0.9] * frames_for(params.min_speech_duration_ms)
    silence = [0.0] * frames_for(params.min_silence_duration_ms)

    events = feed(segmenter, speech + silence + speech + silence)

    assert events.count(VadEvent.SPEECH_STARTED) == 2
    assert events.count(VadEvent.SPEECH_ENDED) == 2


def test_hysteresis_keeps_the_segment_alive_through_a_dip() -> None:
    """**表9。** `exit_threshold`(0.1) と `speech_threshold`(0.3) の間では切らない。"""
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    feed(segmenter, [0.9] * frames_for(params.min_speech_duration_ms))

    dipped = feed(segmenter, [0.2] * frames_for(params.min_silence_duration_ms))

    assert VadEvent.SPEECH_ENDED not in dipped
    assert segmenter.in_speech


def test_the_preroll_keeps_the_start_of_a_word() -> None:
    """**語頭が切れない。** 区間確定より前のフレームを捨てない。

    ここには2つの「前」がある。どちらを落としても語頭が消える。

    | | 何 | 長さ |
    |---|---|---|
    | プリロール | 閾値を超える**前** | `speech_pad_ms`（80 ms） |
    | 候補 | 超えてから**確定するまで** | `min_speech_duration_ms`（250 ms） |
    """
    params = VadParams(speech_pad_ms=96)  # 3 フレーム分
    segmenter = SpeechSegmenter(params)
    speech_frames = frames_for(params.min_speech_duration_ms)

    # 立ち上がりの 1 フレームは閾値未満（プリロール側）
    segmenter.feed(0.1, np.full(WINDOW_SAMPLES, 0.5, dtype=np.float32), playing=False)
    # 閾値は超えているが、まだ確定していない区間（候補側）
    for _ in range(speech_frames):
        segmenter.feed(0.9, np.full(WINDOW_SAMPLES, 0.25, dtype=np.float32), playing=False)
    for _ in range(frames_for(params.min_silence_duration_ms)):
        segmenter.feed(0.0, FRAME, playing=False)

    audio = segmenter.take()

    # プリロールの 0.5 が先頭に残っている
    assert float(audio[0]) == pytest.approx(0.5)
    # **確定までの 250 ms 分（0.25）も残っている。** ここが落ちると語頭が丸ごと消える
    assert float(audio[WINDOW_SAMPLES]) == pytest.approx(0.25)
    assert len(audio) >= WINDOW_SAMPLES * (1 + speech_frames)


def test_take_empties_the_segment() -> None:
    params = VadParams()
    segmenter = SpeechSegmenter(params)
    feed(segmenter, [0.9] * frames_for(params.min_speech_duration_ms))
    feed(segmenter, [0.0] * frames_for(params.min_silence_duration_ms))

    assert len(segmenter.take()) > 0
    assert len(segmenter.take()) == 0


# ── モデル ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def silero() -> SileroVad:
    return SileroVad()


def test_silence_scores_low(silero: SileroVad) -> None:
    """**「聞こえていない」を「声がある」と言わない。**"""
    probability = silero.probability(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
    assert probability < 0.3


def test_the_window_size_is_enforced(silero: SileroVad) -> None:
    """半端な窓を渡されたら**推測せずに落とす。**"""
    with pytest.raises(ValueError, match="512"):
        silero.probability(np.zeros(256, dtype=np.float32))


def test_one_frame_fits_in_the_mute_budget(silero: SileroVad) -> None:
    """**mute latency < 50 ms**（表3）のうち、実装が制御できるのは推論と判定である。

    ここで測っているのは「VAD がフレームを読んでから判定が出るまで」。
    ユーザーの体感（知覚 barge-in latency < 120 ms）はフレーム境界を含むので別物
    → 実機での計測は Step F。
    """
    segmenter = SpeechSegmenter()
    frame = np.random.default_rng(0).normal(0, 0.1, WINDOW_SAMPLES).astype(np.float32)

    for _ in range(5):  # ウォームアップ（初回はセッションの初期化が乗る）
        silero.probability(frame)

    durations: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        probability = silero.probability(frame)
        segmenter.feed(probability, frame, playing=True)
        durations.append(time.perf_counter() - started)

    assert max(durations) < 0.05, f"p50={statistics.median(durations) * 1000:.2f} ms"


def test_reset_clears_the_lstm_state(silero: SileroVad) -> None:
    """セッションを跨いで状態が残ると誤判定する。"""
    noisy = np.random.default_rng(1).normal(0, 0.3, WINDOW_SAMPLES).astype(np.float32)
    silero.probability(noisy)
    silero.reset()
    assert silero.probability(np.zeros(WINDOW_SAMPLES, dtype=np.float32)) < 0.3


# ── 静的検査（表12）─────────────────────────────────────────

CALLBACK_SOURCES = {
    "capture.py": "_callback",
    "playback.py": "_callback",
}

#: **オーディオコールバックに現れてはいけないもの。**
#: 推論・ログ I/O・スリープ・ロック取得は、いずれも締切（数 ms）を脅かす。
FORBIDDEN_IN_CALLBACK = ("probability", "sleep", "acquire", "info", "warning", "exception")


def callback_ast(filename: str, method: str) -> ast.FunctionDef:
    source = (Path(__file__).resolve().parents[1] / "lumi" / "audio" / filename).read_text(
        encoding="utf-8"
    )
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == method:
            return node
    raise AssertionError(f"{filename}: {method} が見つからない")


@pytest.mark.parametrize(("filename", "method"), list(CALLBACK_SOURCES.items()))
def test_audio_callbacks_do_no_inference_or_io(filename: str, method: str) -> None:
    """**コールバック内で推論・ログ・ロックをしない**（表12）。

    ここで ONNX 推論を回すと GIL を取り、バッファアンダーランで
    **barge-in どころか通常の再生が壊れる。**
    """
    called: list[str] = []
    for node in ast.walk(callback_ast(filename, method)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            called.append(name)
        if isinstance(node, ast.With | ast.AsyncWith):
            raise AssertionError(f"{filename}: コールバックで with を使わない（ロックの可能性）")

    offenders = [name for name in called if name in FORBIDDEN_IN_CALLBACK]
    assert offenders == [], f"{filename}: {offenders}"
