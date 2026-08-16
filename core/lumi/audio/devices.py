"""どのデバイスを、どのホスト API で、どのレートで開くか。**純粋関数**。

設計 → docs/architecture/audio.md §8 / docs/decisions/ADR-020-split-audio-streams.md

**入出力は別ストリームで開く。** duplex は使わない（機材によって開けたり開けなかったりするため）。
したがってここが決めるのは「入力の計画」と「出力の計画」であり、両者は独立している。

デバイス一覧の取得（`sounddevice` に触る部分）は `probe.py` にある。
ここは受け取ったテーブルから決めるだけなので、**デバイスが1つも無い CI でもテストできる。**
"""

from __future__ import annotations

from dataclasses import dataclass

#: Windows で優先するホスト API。**実測に基づく**（docs/measurements/phase0.md）。
#: MME は出力遅延 209 ms で barge-in が成立しない。DirectSound は WASAPI の上に乗る分だけ遅い。
PREFERRED_HOST_APIS = ("Windows WASAPI",)

#: 遅延が大きく barge-in に向かないホスト API。選ばざるを得ないときは警告する。
SLOW_HOST_APIS = ("MME", "Windows DirectSound")


@dataclass(frozen=True, slots=True)
class HostApi:
    """ホスト API 1つと、**その中での既定デバイス**。

    既定はホスト API ごとに違う。`sounddevice` の全体既定（`sd.default.device`）は
    PortAudio が選んだホスト API（Windows では MME）のものなので、**そちらを見てはいけない。**
    """

    name: str
    default_input: int | None
    default_output: int | None


@dataclass(frozen=True, slots=True)
class Device:
    """PortAudio のデバイス1つ。**`sounddevice` の辞書をそのまま持ち回らない。**"""

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
    """1本のストリームをどう開くか。"""

    device: Device
    samplerate: int
    channels: int

    def describe(self) -> str:
        return (
            f"{self.device.index}:{self.device.name} / {self.device.host_api} @{self.samplerate}Hz"
        )


@dataclass(frozen=True, slots=True)
class AudioPlan:
    """入力と出力の計画。**入力が無いことは正常な状態**（壊れているのではない）。"""

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
    """**デバイスが受け付けるレートで開く。**

    16 kHz（VAD のレート）で開くことはできない。WASAPI 共有モードは
    エンドポイントの mix format のレートしか受け付けない（実測）。
    16 kHz へは Core 内でリサンプルする（Phase 1）。
    """
    return round(device.default_samplerate) or 48000


def _ordered(host_apis: list[HostApi]) -> list[HostApi]:
    """優先するホスト API を先に、残りは与えられた順に。"""
    preferred = [api for name in PREFERRED_HOST_APIS for api in host_apis if api.name == name]
    return preferred + [api for api in host_apis if api not in preferred]


def _pick(devices: list[Device], host_apis: list[HostApi], *, capture: bool) -> Device | None:
    """**ユーザーが OS で選んだ既定デバイス**を、優先ホスト API から順に探す。

    デバイス列の先頭を採ってはいけない。そこに居るのは「電源の入っていないモニタ」や
    「接続されていない Bluetooth ヘッドセット」であって、ユーザーが選んだものではない（実測）。
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
    # 既定が無いホスト API しか無い場合の最後の手段。**それでも優先順は守る。**
    for host_api in _ordered(host_apis):
        for device in devices:
            chosen = usable(device)
            if chosen is not None and chosen.host_api == host_api.name:
                return chosen
    return None


def plan_audio(devices: list[Device], host_apis: list[HostApi]) -> AudioPlan:
    """開くべきストリームを決める。**デバイスに触らない。**"""
    warnings: list[str] = []

    capture_device = _pick(devices, host_apis, capture=True)
    playback_device = _pick(devices, host_apis, capture=False)

    if capture_device is None:
        # **実在する。** 録音デバイスが1つも有効でない PC は珍しくない。
        # 壊れているのではないので、例外ではなく状態として返す。
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
