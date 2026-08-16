"""Which device, on which host API, at which rate to open. **Pure functions.**

Design → docs/architecture/audio.md §8 / docs/decisions/ADR-020-split-audio-streams.md

**Input and output are opened as separate streams.** Duplex isn't used (whether it
opens or not depends on hardware). So what's decided here is "the input plan" and "the
output plan," independently of each other.

Fetching the device list (the part that touches `sounddevice`) lives in `probe.py`.
This module only decides from a table it's handed, so **it's testable even in CI with
zero devices.**
"""

from __future__ import annotations

from dataclasses import dataclass

#: Preferred host API on Windows. **Based on measurements** (docs/measurements/phase0.md).
#: MME has 209 ms output latency, which rules out barge-in. DirectSound is slower by the layer it adds on top of WASAPI.
PREFERRED_HOST_APIS = ("Windows WASAPI",)

#: Host APIs with high latency, unsuited to barge-in. Warns when there's no choice but to pick one.
SLOW_HOST_APIS = ("MME", "Windows DirectSound")


@dataclass(frozen=True, slots=True)
class HostApi:
    """One host API and **its default device within that API.**

    The default differs per host API. `sounddevice`'s overall default
    (`sd.default.device`) belongs to whichever host API PortAudio picked (MME on
    Windows), so **that one must not be used.**
    """

    name: str
    default_input: int | None
    default_output: int | None


@dataclass(frozen=True, slots=True)
class Device:
    """One PortAudio device. **Never pass `sounddevice`'s raw dict around as-is.**"""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float

    @property
    def can_capture(self) -> bool:
        return self.max_input_channels > 0

    @property
    def can_play(self) -> bool:
        return self.max_output_channels > 0


@dataclass(frozen=True, slots=True)
class StreamPlan:
    """How to open one stream."""

    device: Device
    samplerate: int
    channels: int

    def describe(self) -> str:
        return (
            f"{self.device.index}:{self.device.name} / {self.device.host_api} @{self.samplerate}Hz"
        )


@dataclass(frozen=True, slots=True)
class AudioPlan:
    """The input and output plans. **Having no input is a normal state** (not broken)."""

    capture: StreamPlan | None
    playback: StreamPlan | None
    warnings: tuple[str, ...]

    @property
    def can_listen(self) -> bool:
        return self.capture is not None

    @property
    def can_speak(self) -> bool:
        return self.playback is not None


def _rate_of(device: Device) -> int:
    """**Opens at whatever rate the device accepts.**

    Can't open at 16 kHz (VAD's rate). WASAPI shared mode only accepts the endpoint's
    mix format rate (observed). Resampling to 16 kHz happens inside Core (Phase 1).
    """
    return round(device.default_samplerate) or 48000


def _ordered(host_apis: list[HostApi]) -> list[HostApi]:
    """Preferred host APIs first, the rest in the order given."""
    preferred = [api for name in PREFERRED_HOST_APIS for api in host_apis if api.name == name]
    return preferred + [api for api in host_apis if api not in preferred]


def _pick(devices: list[Device], host_apis: list[HostApi], *, capture: bool) -> Device | None:
    """Searches for **the default device the user picked in the OS**, going through
    preferred host APIs in order.

    Never take the first entry in the device list. What sits there is things like "a
    powered-off monitor" or "a disconnected Bluetooth headset" — not what the user
    chose (observed).
    """
    by_index = {device.index: device for device in devices}

    def usable(device: Device | None) -> Device | None:
        if device is None:
            return None
        return device if (device.can_capture if capture else device.can_play) else None

    for host_api in _ordered(host_apis):
        index = host_api.default_input if capture else host_api.default_output
        chosen = usable(by_index.get(index) if index is not None else None)
        if chosen is not None:
            return chosen
    # Last resort when only host APIs without a default exist. **Preference order is still respected.**
    for host_api in _ordered(host_apis):
        for device in devices:
            chosen = usable(device)
            if chosen is not None and chosen.host_api == host_api.name:
                return chosen
    return None


def plan_audio(devices: list[Device], host_apis: list[HostApi]) -> AudioPlan:
    """Decides which streams to open. **Never touches a device.**"""
    warnings: list[str] = []

    capture_device = _pick(devices, host_apis, capture=True)
    playback_device = _pick(devices, host_apis, capture=False)

    if capture_device is None:
        # **This happens for real.** PCs with zero enabled recording devices are not rare.
        # Not broken, so this is returned as a state, not an exception.
        warnings.append("入力デバイスが1つも無い。音声入力は使えない")
    if playback_device is None:
        warnings.append("出力デバイスが1つも無い。音声出力は使えない")

    for device, role in ((capture_device, "入力"), (playback_device, "出力")):
        if device is not None and device.host_api in SLOW_HOST_APIS:
            warnings.append(
                f"{role}に {device.host_api} を選んだ。"
                "遅延が大きく barge-in が成立しない可能性がある"
            )

    return AudioPlan(
        capture=(
            StreamPlan(capture_device, _rate_of(capture_device), 1)
            if capture_device is not None
            else None
        ),
        playback=(
            StreamPlan(playback_device, _rate_of(playback_device), 1)
            if playback_device is not None
            else None
        ),
        warnings=tuple(warnings),
    )
