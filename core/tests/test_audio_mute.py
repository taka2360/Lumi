"""Muting the microphone (docs/architecture/ui.md §5b).

**Mute closes the input stream.** It is not "keep listening and ignore it": a mute that
leaves the device open leaves the OS microphone indicator lit and leaves Lumi holding
audio it promised not to hear — and the whole value of the control is that it can be
believed.

Nothing here opens a real device. What is tested is the state machine around it, which is
where the failures are: audio surviving a mute, and two VAD workers on one ring.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from lumi.audio.capture import VadWorker
from lumi.audio.devices import AudioPlan, Device, StreamPlan
from lumi.audio.io import AudioIO
from lumi.audio.ring import RingBuffer


class FakeCapture:
    """A `MicrophoneCapture` that opens nothing. **Records what was asked of it.**"""

    def __init__(self, plan: StreamPlan) -> None:
        self._plan = plan
        self._ring = RingBuffer(16_000)
        self.starts = 0
        self.stops = 0

    @property
    def ring(self) -> RingBuffer:
        return self._ring

    @property
    def plan(self) -> StreamPlan:
        return self._plan

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1
        # What the real one does (`MicrophoneCapture.stop`).
        self._ring.clear()

    def wait_until_open(self, timeout: float = 3.0) -> None:
        del timeout


class FakeVad:
    def __init__(self) -> None:
        self.resets = 0

    def probability(self, frame: Any) -> float:
        del frame
        return 0.0

    def reset(self) -> None:
        self.resets += 1


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[AudioIO, FakeCapture]]:
    """`AudioIO` over a capture that opens nothing, **and the fake it is holding.**

    Handed back rather than reached for through `audio._capture`: that attribute is typed
    as the real class, so narrowing it to the fake tells the type checker the rest of the
    test is unreachable.

    **Whatever thread the test started is stopped here.** The VAD worker is real, and a
    test that stubs `VadWorker.stop` out to reproduce a wedged one otherwise leaves it
    reading for the rest of the session.
    """
    from lumi.audio import io as io_module

    created: list[FakeCapture] = []

    def make(plan: StreamPlan) -> FakeCapture:
        capture = FakeCapture(plan)
        created.append(capture)
        return capture

    monkeypatch.setattr(io_module, "MicrophoneCapture", make)
    plan = AudioPlan(
        capture=StreamPlan(device=_device(), samplerate=16_000, channels=1),
        playback=None,
        warnings=(),
    )
    audio = AudioIO(plan)
    # **Taken before the test can patch it.** A fixture is torn down before the
    # `monkeypatch` it depends on, so by the time the lines below run `VadWorker.stop` may
    # still be the stub that made this test's worker refuse to stop.
    stop_worker = VadWorker.stop

    yield audio, created[0]

    worker = audio._vad_worker
    if worker is not None:
        stop_worker(worker)


def _device() -> Device:
    return Device(
        index=0,
        name="fake",
        host_api="fake",
        max_input_channels=1,
        max_output_channels=0,
        default_samplerate=16_000.0,
    )


def test_muting_drops_the_audio_it_captured() -> None:
    """★ **A mute must not become a delay.**

    Whatever is unread in the ring was captured while the user believed nothing was
    listening. Letting the next worker read it means the sentence spoken during a mute is
    transcribed the moment it ends.
    """
    ring = RingBuffer(16_000)
    ring.write(np.ones(1_000, dtype=np.float32))
    assert ring.available == 1_000

    ring.clear()

    assert ring.available == 0


async def test_muting_closes_the_stream_rather_than_ignoring_it(
    rig: tuple[AudioIO, FakeCapture],
) -> None:
    audio, capture = rig
    await audio.start(vad=FakeVad())  # type: ignore[arg-type]

    await audio.set_input_muted(True)

    assert audio.input_muted
    assert capture.stops == 1
    # **No worker left reading.** The device is closed; a worker over a closed stream
    # would be a thread spinning on an empty ring for the length of the mute.
    assert audio._vad_worker is None


async def test_unmuting_reopens_and_forgets_what_came_before(
    rig: tuple[AudioIO, FakeCapture],
) -> None:
    """★ **Silero carries context across frames.**

    A stream resumed minutes later is not a continuation of the one before it, and judging
    the first windows after an unmute against audio from before the mute is how a mute
    leaks into what comes after it.
    """
    audio, capture = rig
    vad = FakeVad()
    await audio.start(vad=vad)  # type: ignore[arg-type]
    resets_before = vad.resets

    await audio.set_input_muted(True)
    await audio.set_input_muted(False)

    assert not audio.input_muted
    assert capture.starts == 2
    assert vad.resets == resets_before + 1

    await audio.set_input_muted(True)


async def test_unmuting_is_refused_while_the_old_worker_is_still_running(
    rig: tuple[AudioIO, FakeCapture], monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ **Never two readers on one ring.**

    Two threads pulling from the same ring each get half the frames — silence that looks
    like a broken microphone. Staying muted is the honest state, and it says so.
    """
    from lumi.audio.capture import CaptureUnavailable

    audio, _capture = rig
    await audio.start(vad=FakeVad())  # type: ignore[arg-type]
    monkeypatch.setattr(VadWorker, "stop", lambda _self: False)

    await audio.set_input_muted(True)
    assert audio.input_muted

    with pytest.raises(CaptureUnavailable):
        await audio.set_input_muted(False)

    # **Still muted.** Reporting success would put the light back on over a stream nobody
    # is reading.
    assert audio.input_muted


async def test_muting_twice_changes_nothing(rig: tuple[AudioIO, FakeCapture]) -> None:
    audio, capture = rig
    await audio.start(vad=FakeVad())  # type: ignore[arg-type]

    await audio.set_input_muted(True)
    await audio.set_input_muted(True)

    assert capture.stops == 1
