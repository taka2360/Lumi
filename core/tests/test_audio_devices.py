"""Device selection (pure functions). **Passes even in CI with zero devices.**

Acceptance criteria → docs/architecture/audio.md §11 tests 15 / 18
"""

from __future__ import annotations

from lumi.audio.devices import Device, HostApi, plan_audio

WASAPI = "Windows WASAPI"
MME = "MME"


def device(
    index: int,
    name: str = "dev",
    host_api: str = WASAPI,
    *,
    input_channels: int = 0,
    output_channels: int = 0,
    rate: float = 48000.0,
) -> Device:
    return Device(
        index=index,
        name=name,
        host_api=host_api,
        max_input_channels=input_channels,
        max_output_channels=output_channels,
        default_samplerate=rate,
    )


def api(
    name: str = WASAPI, *, default_input: int | None = None, default_output: int | None = None
) -> HostApi:
    return HostApi(name=name, default_input=default_input, default_output=default_output)


class TestHostApiSelection:
    def test_wasapi_wins_over_mme(self) -> None:
        """MME has 209 ms output latency (observed). The same hardware appears under both, so **the
        outcome depends on how it's chosen**.
        """
        plan = plan_audio(
            [
                device(0, "mic", MME, input_channels=1, rate=44100),
                device(1, "spk", MME, output_channels=2, rate=44100),
                device(2, "mic", WASAPI, input_channels=1),
                device(3, "spk", WASAPI, output_channels=2),
            ],
            [
                api(MME, default_input=0, default_output=1),
                api(WASAPI, default_input=2, default_output=3),
            ],
        )
        assert plan.capture is not None and plan.capture.device.index == 2
        assert plan.playback is not None and plan.playback.device.index == 3
        assert plan.warnings == ()

    def test_falls_back_to_mme_and_says_so(self) -> None:
        """**Never silently falls back to the slow path.** Falling back is fine, but it's always
        reported.
        """
        plan = plan_audio(
            [
                device(0, "mic", MME, input_channels=1, rate=44100),
                device(1, "spk", MME, output_channels=2, rate=44100),
            ],
            [api(MME, default_input=0, default_output=1)],
        )
        assert plan.capture is not None and plan.capture.device.host_api == MME
        assert any("barge-in" in warning for warning in plan.warnings)


class TestDefaultDevice:
    def test_uses_the_os_default_not_the_first_device(self) -> None:
        """**Never takes the first entry in the list.**

        On a machine this was observed on, the first WASAPI entries were "a
        powered-off monitor" and "a disconnected Bluetooth headset" — both would
        open but never deliver frames. The device the user picked in the OS is used instead.
        """
        plan = plan_audio(
            [
                device(0, "電源の入っていないモニタ", output_channels=2),
                device(1, "切れている BT ヘッドセット", input_channels=1, rate=16000),
                device(2, "USB マイク", input_channels=2),
                device(3, "USB ヘッドホン", output_channels=2, rate=96000),
            ],
            [api(WASAPI, default_input=2, default_output=3)],
        )
        assert plan.capture is not None and plan.capture.device.index == 2
        assert plan.playback is not None and plan.playback.device.index == 3

    def test_ignores_a_default_that_points_the_wrong_way(self) -> None:
        """If the default input points at an output-only device, that default can't be used."""
        plan = plan_audio(
            [
                device(0, "spk", output_channels=2),
                device(1, "mic", input_channels=2),
            ],
            [api(WASAPI, default_input=0, default_output=0)],
        )
        assert plan.capture is not None and plan.capture.device.index == 1

    def test_skips_a_host_api_that_has_no_default(self) -> None:
        """If WASAPI has no default, the next host API is checked."""
        plan = plan_audio(
            [
                device(0, "mic", MME, input_channels=1, rate=44100),
                device(1, "spk", MME, output_channels=2, rate=44100),
            ],
            [
                api(WASAPI, default_input=None, default_output=None),
                api(MME, default_input=0, default_output=1),
            ],
        )
        assert plan.capture is not None and plan.capture.device.index == 0
        assert plan.playback is not None and plan.playback.device.index == 1


class TestSampleRate:
    def test_opens_at_the_device_rate_not_the_vad_rate(self) -> None:
        """**Can't be opened at 16 kHz** (WASAPI shared mode only accepts the mix format).

        VAD's 16 kHz is produced by resampling inside Core.
        """
        plan = plan_audio(
            [device(0, "mic", input_channels=1, rate=48000)],
            [api(WASAPI, default_input=0)],
        )
        assert plan.capture is not None
        assert plan.capture.samplerate == 48000

    def test_input_and_output_rates_are_independent(self) -> None:
        """Input and output are separate streams, so **there's no need for them to share a rate**.

        A USB headset observed in practice was exactly this case (mic at 48k,
        headphones at 96k) — a combination that couldn't be opened in duplex.
        """
        plan = plan_audio(
            [
                device(0, "headset mic", input_channels=1, rate=48000),
                device(1, "headset out", output_channels=2, rate=96000),
            ],
            [api(WASAPI, default_input=0, default_output=1)],
        )
        assert plan.capture is not None and plan.capture.samplerate == 48000
        assert plan.playback is not None and plan.playback.samplerate == 96000


class TestMissingDevices:
    def test_no_input_device_is_a_state_not_an_error(self) -> None:
        """**This happens for real** (the initial state of a machine observed in practice). Returned
        as a state, not raised as an exception.
        """
        plan = plan_audio(
            [device(0, "spk", output_channels=2)],
            [api(WASAPI, default_input=None, default_output=0)],
        )
        assert plan.capture is None
        assert not plan.can_listen
        assert plan.can_speak
        assert any("audio input device" in warning for warning in plan.warnings)

    def test_no_devices_at_all(self) -> None:
        plan = plan_audio([], [])
        assert not plan.can_listen
        assert not plan.can_speak
        assert len(plan.warnings) == 2
