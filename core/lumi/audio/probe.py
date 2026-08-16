"""Connectivity check and clock-drift measurement for audio devices.

Makes roadmap Phase 0 verification step 12 (**confirming streams can open when mic and
speaker are different devices**) **reproducible on another machine**.

```
uv run python -m lumi.audio.probe            # 30 seconds
uv run python -m lumi.audio.probe --seconds 120
```

Only writes silence, so nothing is audible. Microphone audio is **neither recorded nor
transmitted** (only the frame count is tallied).

**This is not the product's audio path.** Phase 1's AudioIO (ring / VAD / mute) is built
separately. The callback here records timestamps for measurement purposes, so **don't
use it as a model for Phase 1's callback** (the standard for what's allowed under
real-time constraints → docs/architecture/audio.md §3).
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

#: Frames per callback. Smaller lowers latency but tightens the callback's deadline.
BLOCK_SIZE = 512

#: **Treated as "not connected" if the first frame doesn't arrive within this time.**
#: Some devices exist where `open()` succeeds but not a single frame ever arrives (disabled endpoints).
FIRST_FRAME_TIMEOUT_S = 3.0

DEFAULT_SECONDS = 30.0


class _Recorder:
    """Records timestamps and frame counts from the callback.

    **Arrays are pre-allocated up front.** Appending to a growable list inside the
    callback can cause a reallocation that blows the deadline (the measurement would
    corrupt what it's measuring).
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
    """Result for one stream. **Keeps "opened" and "flowing" as separate facts.**"""

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
            # **Don't discard the reason.** "No frame arrived" and "died partway through" are different things.
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
    """PortAudio represents "no default" as -1. **Never treat -1 as a device index.**"""
    index = int(value)
    return index if index >= 0 else None


def list_devices() -> tuple[list[Device], list[HostApi]]:
    """Fetches PortAudio's device list. **The only place that touches `sounddevice`.**"""
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
    """One open stream.

    **Stream creation, start, and stop all happen on the same thread.**
    Windows's WASAPI uses COM, and COM needs per-thread initialization.
    Opening from a different thread crashes with `Unanticipated host error` (observed).
    The measurement itself proceeds on the callback (PortAudio's own thread), so opening
    both from the main thread lets input and output run simultaneously.
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
    except Exception as error:  # sounddevice raises PortAudioError
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
        # If the callback raised, this is the first place it surfaces. **Never swallow it.**
        session.error = session.error or str(error)


def probe(seconds: float = DEFAULT_SECONDS) -> ProbeReport:
    """Plan, open, and measure. **Runs input and output simultaneously** (to compare their clocks)."""
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

    # **Opening alone isn't connectivity.** Wait until the first frame arrives.
    for session in sessions.values():
        if session.opened:
            session.flowing = session.recorder.first_frame.wait(FIRST_FRAME_TIMEOUT_S)

    if any(session.flowing for session in sessions.values()):
        # **Measure while keeping input and output running simultaneously** (to compare their clocks).
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
