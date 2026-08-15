"""TTS セットアップ状態と「もう聞いたか」の記録。"""

from __future__ import annotations

from pathlib import Path

from lumi.setup.state import SetupAnswers, TtsSetup, TtsSetupState


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
