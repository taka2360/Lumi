"""音声デバイスの開通確認とクロックドリフトの実測。

roadmap Phase 0 検証手順 12（**マイクとスピーカーが別デバイスの構成で
ストリームが開けるか確認**）を、**別のマシンでも再現できる形**にしたもの。

```
uv run python -m lumi.audio.probe            # 30 秒
uv run python -m lumi.audio.probe --seconds 120
```

無音しか書かないので音は出ない。マイクの音は**記録も送信もしない**（フレーム数だけ数える）。

**これは製品の音声経路ではない。** Phase 1 の AudioIO（リング・VAD・ミュート）は別に作る。
ここのコールバックは計測のために時刻を記録するので、**Phase 1 のコールバックの手本にしてはいけない**
（リアルタイム制約下でやってよいことの基準 → docs/architecture/audio.md §3）。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from array import array
from dataclasses import dataclass, field
from typing import Any

from lumi.audio.devices import AudioPlan, Device, HostApi, StreamPlan, plan_audio
from lumi.audio.drift import DriftEstimate, drift_ms, estimate_drift, relative_ppm

#: 1コールバックあたりのフレーム数。小さいほど遅延が下がり、コールバックの締切が厳しくなる。
BLOCK_SIZE = 512

#: **最初のフレームがこの時間内に来なければ「開通していない」と扱う。**
#: `open()` は成功するのにフレームが1つも来ないデバイスが実在する（無効化されたエンドポイント）。
FIRST_FRAME_TIMEOUT_S = 3.0

DEFAULT_SECONDS = 30.0


class _Recorder:
    """コールバックから時刻とフレーム数を記録する。

    **配列を先に確保しておく。** コールバック内で伸びる list に append すると、
    再確保が締切を踏み越えることがある（計測が計測対象を壊す）。
    """

    def __init__(self, samplerate: int, seconds: float) -> None:
        capacity = int(seconds * samplerate / BLOCK_SIZE) + 256
        self._wall = array("d", bytes(8 * capacity))
        self._stream = array("d", bytes(8 * capacity))
        self._capacity = capacity
        self._rate = samplerate
        self.count = 0
        self.frames = 0
        self.xruns = 0
        self.first_frame_at: float | None = None
        self.first_frame = threading.Event()

    def record(self, frames: int, *, bad_status: bool) -> None:
        now = time.perf_counter()
        if bad_status:
            self.xruns += 1
        self.frames += frames
        if self.first_frame_at is None and frames > 0:
            self.first_frame_at = now
            self.first_frame.set()
        if self.count < self._capacity:
            self._wall[self.count] = now
            self._stream[self.count] = self.frames / self._rate
            self.count += 1

    def samples(self) -> list[tuple[float, float]]:
        return [(self._wall[i], self._stream[i]) for i in range(self.count)]


@dataclass(frozen=True, slots=True)
class StreamReport:
    """1本のストリームの結果。**開けたことと流れたことを分けて持つ。**"""

    plan: StreamPlan
    opened: bool
    flowing: bool
    error: str = ""
    frames: int = 0
    callbacks: int = 0
    xruns: int = 0
    first_frame_s: float | None = None
    latency_s: float | None = None
    drift: DriftEstimate | None = None

    def lines(self, role: str) -> list[str]:
        out = [f"■ {role}: {self.plan.describe()}"]
        if not self.opened:
            out.append(f"    ✗ 開けなかった: {self.error}")
            return out
        if not self.flowing:
            out.append(
                f"    ✗ 開けたがフレームが来ない（{FIRST_FRAME_TIMEOUT_S:.0f} 秒待った）。"
                "**開通していないものとして扱う**"
            )
            # **理由を捨てない。** 「フレームが来ない」と「途中で落ちた」は別の話。
            if self.error:
                out.append(f"      理由: {self.error}")
            return out
        out.append(
            f"    ✓ 開通 / 最初のフレームまで {self.first_frame_s:.3f} 秒"
            + (f" / 遅延 {self.latency_s * 1000:.0f} ms" if self.latency_s is not None else "")
        )
        out.append(
            f"      フレーム {self.frames} / コールバック {self.callbacks} / xrun {self.xruns}"
        )
        if self.drift is not None:
            out.append(
                f"      実時間比 {self.drift.ppm:+.1f} ppm"
                f"（残差 {self.drift.residual_ms:.2f} ms, n={self.drift.samples}）"
            )
        return out


@dataclass(frozen=True, slots=True)
class ProbeReport:
    plan: AudioPlan
    capture: StreamReport | None = None
    playback: StreamReport | None = None
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines: list[str] = []
        for warning in self.plan.warnings:
            lines.append(f"⚠ {warning}")
        if self.capture is not None:
            lines += self.capture.lines("入力")
        if self.playback is not None:
            lines += self.playback.lines("出力")
        if self.capture is not None and self.playback is not None:
            capture_drift = self.capture.drift
            playback_drift = self.playback.drift
            if capture_drift is not None and playback_drift is not None:
                ppm = relative_ppm(capture_drift, playback_drift)
                lines.append(
                    f"■ 入出力の相対ドリフト: {ppm:+.1f} ppm"
                    f" → {drift_ms(ppm, 60.0):.2f} ms/分, {drift_ms(ppm, 3600.0):.1f} ms/時"
                )
                lines.append(
                    "    ※ 測ったのは**アプリから見たストリームのペース**であり、"
                    "スピーカーからマイクまでの実遅延ではない"
                )
        lines += [f"  {note}" for note in self.notes]
        return "\n".join(lines)


def _optional_index(value: Any) -> int | None:
    """PortAudio は「既定が無い」を -1 で表す。**-1 をデバイス番号として扱わない。**"""
    index = int(value)
    return index if index >= 0 else None


def list_devices() -> tuple[list[Device], list[HostApi]]:
    """PortAudio のデバイス一覧を取る。**ここだけが `sounddevice` に触る。**"""
    import sounddevice as sd

    raw_apis = list(sd.query_hostapis())
    host_apis = [
        HostApi(
            name=str(api["name"]),
            default_input=_optional_index(api["default_input_device"]),
            default_output=_optional_index(api["default_output_device"]),
        )
        for api in raw_apis
    ]
    devices = [
        Device(
            index=index,
            name=str(raw["name"]),
            host_api=str(raw_apis[raw["hostapi"]]["name"]),
            max_input_channels=int(raw["max_input_channels"]),
            max_output_channels=int(raw["max_output_channels"]),
            default_samplerate=float(raw["default_samplerate"]),
        )
        for index, raw in enumerate(sd.query_devices())
    ]
    return devices, host_apis


@dataclass
class _Session:
    """開いている1本のストリーム。

    **ストリームの生成・開始・停止はすべて同じスレッドで行う。**
    Windows の WASAPI は COM を使い、COM はスレッドごとに初期化が要る。
    別スレッドで開くと `Unanticipated host error` で落ちる（実測）。
    測定自体はコールバック（PortAudio 側のスレッド）で進むので、
    メインスレッドから2本開けば入出力は同時に走る。
    """

    plan: StreamPlan
    recorder: _Recorder
    stream: Any = None
    opened: bool = False
    error: str = ""
    started: float = 0.0
    latency: float | None = None
    flowing: bool = False

    def report(self) -> StreamReport:
        first = self.recorder.first_frame_at
        return StreamReport(
            plan=self.plan,
            opened=self.opened,
            flowing=self.flowing,
            error=self.error,
            frames=self.recorder.frames,
            callbacks=self.recorder.count,
            xruns=self.recorder.xruns,
            first_frame_s=None if first is None else first - self.started,
            latency_s=self.latency,
            drift=estimate_drift(self.recorder.samples()) if self.flowing else None,
        )


def _open(plan: StreamPlan, seconds: float, *, capture: bool) -> _Session:
    import sounddevice as sd

    recorder = _Recorder(plan.samplerate, seconds)
    session = _Session(plan=plan, recorder=recorder)
    silence = bytes(BLOCK_SIZE * plan.channels * 2 * 2)

    def capture_callback(indata: Any, frames: int, _time: Any, status: Any) -> None:
        recorder.record(frames, bad_status=bool(status))

    def playback_callback(outdata: Any, frames: int, _time: Any, status: Any) -> None:
        outdata[:] = silence[: len(outdata)]
        recorder.record(frames, bad_status=bool(status))

    common: dict[str, Any] = {
        "samplerate": plan.samplerate,
        "blocksize": BLOCK_SIZE,
        "device": plan.device.index,
        "channels": plan.channels,
        "dtype": "int16",
    }
    try:
        stream = (
            sd.RawInputStream(callback=capture_callback, **common)
            if capture
            else sd.RawOutputStream(callback=playback_callback, **common)
        )
        session.started = time.perf_counter()
        stream.start()
    except Exception as error:  # sounddevice は PortAudioError を投げる
        session.error = str(error)
        return session

    session.stream = stream
    session.opened = True
    session.latency = float(stream.latency)
    return session


def _close(session: _Session) -> None:
    if session.stream is None:
        return
    try:
        session.stream.stop()
        session.stream.close()
    except Exception as error:
        # コールバックが投げていた場合、ここで初めて分かる。**握り潰さない。**
        session.error = session.error or str(error)


def probe(seconds: float = DEFAULT_SECONDS) -> ProbeReport:
    """計画を立て、開き、測る。**入力と出力を同時に走らせる**（互いのクロックを比べるため）。"""
    try:
        devices, host_apis = list_devices()
    except OSError as error:
        plan = AudioPlan(capture=None, playback=None, warnings=(f"PortAudio が使えない: {error}",))
        return ProbeReport(plan=plan)

    plan = plan_audio(devices, host_apis)
    sessions = {
        role: _open(stream_plan, seconds, capture=role == "capture")
        for role, stream_plan in (("capture", plan.capture), ("playback", plan.playback))
        if stream_plan is not None
    }

    # **開いただけでは開通ではない。** 最初のフレームが来るまで待つ。
    for session in sessions.values():
        if session.opened:
            session.flowing = session.recorder.first_frame.wait(FIRST_FRAME_TIMEOUT_S)

    if any(session.flowing for session in sessions.values()):
        # **入出力を同時に走らせたまま測る**（互いのクロックを比べるため）。
        time.sleep(seconds)

    for session in sessions.values():
        _close(session)

    return ProbeReport(
        plan=plan,
        capture=sessions["capture"].report() if "capture" in sessions else None,
        playback=sessions["playback"].report() if "playback" in sessions else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="音声デバイスの開通確認とドリフト実測")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    args = parser.parse_args()

    report = probe(args.seconds)
    print(report.render())
    ok = (report.capture is None or report.capture.flowing) and (
        report.playback is not None and report.playback.flowing
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
