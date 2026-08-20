"""TTS setup state and the record of "was it already asked."""

from __future__ import annotations

from pathlib import Path

from lumi.setup.state import (
    BootPhase,
    EngineRuntime,
    LlmSetup,
    LlmSetupState,
    SetupAnswers,
    SetupSnapshot,
    SttSetup,
    SttSetupState,
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
        """**Distinguishes "never fetched" from "failed."**"""
        failed = TtsSetup(state=TtsSetupState.FAILED, reason="hash_mismatch")
        payload = failed.to_payload()
        assert payload["state"] == "failed"
        assert payload["reason"] == "hash_mismatch"

        not_configured = TtsSetup(state=TtsSetupState.NOT_CONFIGURED).to_payload()
        assert not_configured["state"] == "not_configured"
        assert not_configured["reason"] is None


class TestBootPhase:
    """**A pure function that decides whether the character may be shown**
    (docs/architecture/ui.md).
    """

    def test_waiting_for_the_user(self) -> None:
        setup = TtsSetup(state=TtsSetupState.NOT_CONFIGURED)
        assert boot_phase(SetupSnapshot(tts=setup), prompting=True) is BootPhase.SETUP

    def test_installing(self) -> None:
        setup = TtsSetup(state=TtsSetupState.INSTALLING, progress=0.3)
        assert boot_phase(SetupSnapshot(tts=setup), prompting=False) is BootPhase.INSTALLING

    def test_engine_starting(self) -> None:
        setup = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.STARTING)
        assert boot_phase(SetupSnapshot(tts=setup), prompting=False) is BootPhase.STARTING

    def test_an_engine_that_has_not_been_started_yet_is_not_ready(self) -> None:
        """**Never shows the character only to pull it back.**

        If a usable engine exists, it's about to start. It's just not running yet;
        marking it `READY` would make the Stage show the character only for startup
        to begin and make it vanish right after.
        """
        for state in (TtsSetupState.INSTALLED, TtsSetupState.DETECTED):
            setup = TtsSetup(state=state, runtime=EngineRuntime.STOPPED)
            assert boot_phase(SetupSnapshot(tts=setup), prompting=False) is BootPhase.STARTING

    def test_ready_when_the_engine_is_up(self) -> None:
        setup = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.READY)
        assert boot_phase(SetupSnapshot(tts=setup), prompting=False) is BootPhase.READY

    def test_declining_the_download_still_shows_the_character(self) -> None:
        """**Being unable to speak and Lumi not having started are different things.**

        A user who chose not to fetch is never trapped on a loading screen.
        """
        setup = TtsSetup(state=TtsSetupState.NOT_CONFIGURED)
        assert boot_phase(SetupSnapshot(tts=setup), prompting=False) is BootPhase.READY

    def test_a_failure_still_shows_the_character(self) -> None:
        """**The character is never held hostage.** Something being broken is reported elsewhere."""
        setup = TtsSetup(state=TtsSetupState.FAILED, reason="hash_mismatch")
        assert boot_phase(SetupSnapshot(tts=setup), prompting=False) is BootPhase.READY

        broken = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.FAILED)
        assert boot_phase(SetupSnapshot(tts=broken), prompting=False) is BootPhase.READY

    def test_the_phase_is_in_the_payload(self) -> None:
        snapshot = SetupSnapshot(tts=TtsSetup(state=TtsSetupState.NOT_CONFIGURED))
        assert snapshot.to_payload(prompting=True)["boot"] == "setup"

    def test_fetching_the_speech_model_also_waits(self) -> None:
        """**STT is fetched with consent too**, and a fetch with progress is exactly the
        case where waiting is honest (docs/architecture/setup.md §2b).
        """
        snapshot = SetupSnapshot(
            tts=TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.READY),
            stt=SttSetup(state=SttSetupState.INSTALLING, progress=0.2),
        )
        assert boot_phase(snapshot, prompting=False) is BootPhase.INSTALLING

    def test_no_speech_model_still_shows_the_character(self) -> None:
        """**"Speaks but can't listen" is a normal state**, not a reason to keep waiting."""
        snapshot = SetupSnapshot(
            tts=TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.READY),
            stt=SttSetup(state=SttSetupState.NOT_CONFIGURED),
        )
        assert boot_phase(snapshot, prompting=False) is BootPhase.READY

    def test_the_llm_never_holds_up_the_character(self) -> None:
        """**Lumi neither fetches nor starts Ollama**, so waiting accomplishes nothing
        (docs/architecture/setup.md §2b). No LLM is a Lumi that listens and doesn't answer.
        """
        ready = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.READY)
        for state in LlmSetupState:
            snapshot = SetupSnapshot(tts=ready, llm=LlmSetup(state=state))
            assert boot_phase(snapshot, prompting=False) is BootPhase.READY, state

    def test_every_component_appears_in_the_payload(self) -> None:
        """The Stage cannot show what it was never sent."""
        payload = SetupSnapshot().to_payload()
        assert set(payload) == {"boot", "tts", "llm", "stt"}


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
