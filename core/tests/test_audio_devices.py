"""デバイス選択（純粋関数）。**デバイスが1つも無い CI でも通る。**

受け入れ条件 → docs/architecture/audio.md §11 テスト 15 / 18
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
        """MME は出力遅延 209 ms（実測）。同じ機材が両方に出てくるので、**選び方で決まる**。"""
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
        """**黙って遅い経路に落ちない。** 落ちること自体は許すが、必ず言う。"""
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
        """**列の先頭を採らない。**

        実測したマシンでは、WASAPI の先頭が「電源の入っていないモニタ」と
        「接続されていない Bluetooth ヘッドセット」で、どちらも開けるのにフレームが来なかった。
        ユーザーが OS で選んだデバイスを使う。
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
        """既定入力に出力専用デバイスが入っていたら、それは既定として使えない。"""
        plan = plan_audio(
            [
                device(0, "spk", output_channels=2),
                device(1, "mic", input_channels=2),
            ],
            [api(WASAPI, default_input=0, default_output=0)],
        )
        assert plan.capture is not None and plan.capture.device.index == 1

    def test_skips_a_host_api_that_has_no_default(self) -> None:
        """WASAPI に既定が無ければ、次のホスト API を見る。"""
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
        """**16 kHz では開けない**（WASAPI 共有モードは mix format のみ）。

        VAD の 16 kHz は Core 内でリサンプルして作る。
        """
        plan = plan_audio(
            [device(0, "mic", input_channels=1, rate=48000)],
            [api(WASAPI, default_input=0)],
        )
        assert plan.capture is not None
        assert plan.capture.samplerate == 48000

    def test_input_and_output_rates_are_independent(self) -> None:
        """入出力は別ストリームなので、**同じレートである必要が無い**。

        実測した USB ヘッドセットがまさにこれ（マイク 48k / ヘッドホン 96k）で、
        duplex では開けなかった組み合わせ。
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
        """**実在する**（実測したマシンの初期状態）。例外にせず、状態として返す。"""
        plan = plan_audio(
            [device(0, "spk", output_channels=2)],
            [api(WASAPI, default_input=None, default_output=0)],
        )
        assert plan.capture is None
        assert not plan.can_listen
        assert plan.can_speak
        assert any("入力デバイス" in warning for warning in plan.warnings)

    def test_no_devices_at_all(self) -> None:
        plan = plan_audio([], [])
        assert not plan.can_listen
        assert not plan.can_speak
        assert len(plan.warnings) == 2
