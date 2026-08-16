"""TTS セットアップ状態と「もう聞いたか」の記録。"""

from __future__ import annotations

from pathlib import Path

from lumi.setup.state import (
    BootPhase,
    EngineRuntime,
    SetupAnswers,
    TtsSetup,
    TtsSetupState,
    boot_phase,
)


class TestTtsSetup:
    def test_only_detected_and_installed_can_speak(self) -> None:
        assert TtsSetup(state=TtsSetupState.DETECTED).usable
        assert TtsSetup(state=TtsSetupState.INSTALLED).usable
        for state in (
            TtsSetupState.UNKNOWN,
            TtsSetupState.NOT_CONFIGURED,
            TtsSetupState.INSTALLING,
            TtsSetupState.FAILED,
        ):
            assert not TtsSetup(state=state).usable

    def test_failure_carries_a_reason(self) -> None:
        """**「取得しなかった」と「失敗した」を区別する。**"""
        failed = TtsSetup(state=TtsSetupState.FAILED, reason="hash_mismatch")
        payload = failed.to_payload()
        assert payload["state"] == "failed"
        assert payload["reason"] == "hash_mismatch"

        not_configured = TtsSetup(state=TtsSetupState.NOT_CONFIGURED).to_payload()
        assert not_configured["state"] == "not_configured"
        assert not_configured["reason"] is None


class TestBootPhase:
    """**キャラクターを出してよいかを決める純粋関数**（docs/architecture/ui.md）。"""

    def test_waiting_for_the_user(self) -> None:
        setup = TtsSetup(state=TtsSetupState.NOT_CONFIGURED)
        assert boot_phase(setup, prompting=True) is BootPhase.SETUP

    def test_installing(self) -> None:
        setup = TtsSetup(state=TtsSetupState.INSTALLING, progress=0.3)
        assert boot_phase(setup, prompting=False) is BootPhase.INSTALLING

    def test_engine_starting(self) -> None:
        setup = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.STARTING)
        assert boot_phase(setup, prompting=False) is BootPhase.STARTING

    def test_an_engine_that_has_not_been_started_yet_is_not_ready(self) -> None:
        """**キャラクターを出してから引っ込めない。**

        使えるエンジンがあるなら、これから起動する。まだ止まっているだけで
        `READY` にすると、Stage がキャラクターを出した直後に起動が始まって消える。
        """
        for state in (TtsSetupState.INSTALLED, TtsSetupState.DETECTED):
            setup = TtsSetup(state=state, runtime=EngineRuntime.STOPPED)
            assert boot_phase(setup, prompting=False) is BootPhase.STARTING

    def test_ready_when_the_engine_is_up(self) -> None:
        setup = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.READY)
        assert boot_phase(setup, prompting=False) is BootPhase.READY

    def test_declining_the_download_still_shows_the_character(self) -> None:
        """**喋れないことと、Lumi が起動していないことは別。**

        取得しない選択をしたユーザーを、ローディング画面に閉じ込めない。
        """
        setup = TtsSetup(state=TtsSetupState.NOT_CONFIGURED)
        assert boot_phase(setup, prompting=False) is BootPhase.READY

    def test_a_failure_still_shows_the_character(self) -> None:
        """**キャラクターを人質にしない。** 壊れていることは別の場所が言う。"""
        setup = TtsSetup(state=TtsSetupState.FAILED, reason="hash_mismatch")
        assert boot_phase(setup, prompting=False) is BootPhase.READY

        broken = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.FAILED)
        assert boot_phase(broken, prompting=False) is BootPhase.READY

    def test_the_phase_is_in_the_payload(self) -> None:
        payload = TtsSetup(state=TtsSetupState.NOT_CONFIGURED).to_payload(prompting=True)
        assert payload["boot"] == "setup"


class TestSetupAnswers:
    def test_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "setup.json"
        SetupAnswers(tts_prompt_answered=True).save(path)
        assert SetupAnswers.load(path).tts_prompt_answered

    def test_missing_file_means_not_asked_yet(self, tmp_path: Path) -> None:
        assert not SetupAnswers.load(tmp_path / "absent.json").tts_prompt_answered

    def test_a_broken_file_does_not_stop_startup(self, tmp_path: Path) -> None:
        path = tmp_path / "setup.json"
        path.write_text("{ broken", encoding="utf-8")
        assert not SetupAnswers.load(path).tts_prompt_answered
