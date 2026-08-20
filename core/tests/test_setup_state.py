"""Setup state, and the phase derived from it.

Tests 21 / 22 from docs/architecture/setup.md §8 live here — **all three components have
to be usable before the character comes out** (ADR-034), and a failed fetch never
resolves to "ready."
"""

from __future__ import annotations

import pytest

from lumi.setup.state import (
    BootPhase,
    EngineRuntime,
    LlmSetup,
    LlmSetupState,
    SetupSnapshot,
    SttSetup,
    SttSetupState,
    TtsSetup,
    TtsSetupState,
    boot_phase,
)

#: The three states that together mean "Lumi can hold a conversation." Used as the
#: baseline so a test that changes one component isn't accidentally blocked by another.
WORKING_TTS = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.READY)
WORKING_LLM = LlmSetup(state=LlmSetupState.DETECTED, runtime=EngineRuntime.READY)
WORKING_STT = SttSetup(state=SttSetupState.INSTALLED, model="small", runtime=EngineRuntime.READY)


def snapshot(
    *,
    tts: TtsSetup = WORKING_TTS,
    llm: LlmSetup = WORKING_LLM,
    stt: SttSetup = WORKING_STT,
) -> SetupSnapshot:
    return SetupSnapshot(tts=tts, llm=llm, stt=stt)


class TestTtsSetup:
    def test_only_detected_and_installed_are_installed(self) -> None:
        assert TtsSetup(state=TtsSetupState.DETECTED).installed
        assert TtsSetup(state=TtsSetupState.INSTALLED).installed
        for state in (
            TtsSetupState.UNKNOWN,
            TtsSetupState.NOT_CONFIGURED,
            TtsSetupState.INSTALLING,
            TtsSetupState.FAILED,
        ):
            assert not TtsSetup(state=state).installed

    def test_installed_is_not_ready(self) -> None:
        """**Both axes have to agree** (docs/architecture/setup.md §2).

        An engine on disk that will not start is the exact case a single flag erases.
        """
        for runtime in (EngineRuntime.STOPPED, EngineRuntime.STARTING, EngineRuntime.FAILED):
            engine = TtsSetup(state=TtsSetupState.INSTALLED, runtime=runtime)
            assert engine.installed
            assert not engine.ready, runtime
        assert TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.READY).ready

    def test_failure_carries_a_reason(self) -> None:
        """**Distinguishes "never fetched" from "failed."**"""
        failed = TtsSetup(state=TtsSetupState.FAILED, reason="hash_mismatch")
        payload = failed.to_payload()
        assert payload["state"] == "failed"
        assert payload["reason"] == "hash_mismatch"

        not_configured = TtsSetup(state=TtsSetupState.NOT_CONFIGURED).to_payload()
        assert not_configured["state"] == "not_configured"
        assert not_configured["reason"] is None


class TestLlmSetup:
    def test_a_missing_model_is_not_ready(self) -> None:
        """**Ollama answering is not the same as it being able to run Lumi's model.**"""
        answering = LlmSetup(state=LlmSetupState.MODEL_MISSING, runtime=EngineRuntime.READY)
        assert not answering.ready

    def test_installed_but_stopped_is_not_ready(self) -> None:
        assert not LlmSetup(state=LlmSetupState.DETECTED, runtime=EngineRuntime.STOPPED).ready
        assert LlmSetup(state=LlmSetupState.DETECTED, runtime=EngineRuntime.READY).ready


class TestSttSetup:
    def test_installed_is_not_ready_until_the_provider_loads(self) -> None:
        for runtime in (EngineRuntime.STOPPED, EngineRuntime.STARTING, EngineRuntime.FAILED):
            stt = SttSetup(state=SttSetupState.INSTALLED, runtime=runtime)
            assert not stt.ready, runtime
        assert SttSetup(state=SttSetupState.INSTALLED, runtime=EngineRuntime.READY).ready

    def test_payload_keeps_acquisition_and_runtime_separate(self) -> None:
        payload = SttSetup(
            state=SttSetupState.INSTALLED,
            model="small",
            runtime=EngineRuntime.FAILED,
        ).to_payload()

        assert payload["state"] == "installed"
        assert payload["runtime"] == "failed"


class TestBootPhase:
    """**A pure function that decides whether the character may be shown**
    (docs/architecture/ui.md).
    """

    def test_waiting_for_the_user(self) -> None:
        assert boot_phase(snapshot(), prompting=True) is BootPhase.SETUP

    def test_installing(self) -> None:
        fetching = TtsSetup(state=TtsSetupState.INSTALLING, progress=0.3)
        assert boot_phase(snapshot(tts=fetching), prompting=False) is BootPhase.INSTALLING

    def test_engine_starting(self) -> None:
        starting = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.STARTING)
        assert boot_phase(snapshot(tts=starting), prompting=False) is BootPhase.STARTING

    def test_speech_provider_starting(self) -> None:
        starting = SttSetup(state=SttSetupState.INSTALLED, runtime=EngineRuntime.STARTING)
        assert boot_phase(snapshot(stt=starting), prompting=False) is BootPhase.STARTING

    def test_an_engine_that_has_not_been_started_yet_is_not_ready(self) -> None:
        """**Never shows the character only to pull it back.**

        If a usable engine exists, it's about to start. It's just not running yet;
        marking it `READY` would make the Stage show the character only for startup
        to begin and make it vanish right after.
        """
        for state in (TtsSetupState.INSTALLED, TtsSetupState.DETECTED):
            stopped = TtsSetup(state=state, runtime=EngineRuntime.STOPPED)
            assert boot_phase(snapshot(tts=stopped), prompting=False) is BootPhase.STARTING

    def test_ready_needs_all_three(self) -> None:
        assert boot_phase(snapshot(), prompting=False) is BootPhase.READY

    def test_declining_the_download_blocks_startup(self) -> None:
        """★ **Reversed by ADR-034.** This used to be `READY`.

        A character standing on the desktop that can never speak reads as broken, not as
        unfinished — the loading screen's own reasoning, applied to a wait that never ends.
        """
        declined = TtsSetup(state=TtsSetupState.NOT_CONFIGURED)
        assert boot_phase(snapshot(tts=declined), prompting=False) is BootPhase.BLOCKED

    def test_a_failed_fetch_is_never_ready(self) -> None:
        """★ **A fetch that failed must not be able to read as a successful start**
        (docs/architecture/setup.md §8 test 22).

        This was the worst of the three: the character coming out **overwrote** "could not
        download" with "started successfully."
        """
        failed = TtsSetup(state=TtsSetupState.FAILED, reason="hash_mismatch")
        assert boot_phase(snapshot(tts=failed), prompting=False) is BootPhase.BLOCKED

        broken = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.FAILED)
        assert boot_phase(snapshot(tts=broken), prompting=False) is BootPhase.BLOCKED

    def test_a_failed_speech_provider_is_installed_but_blocked(self) -> None:
        broken = SttSetup(state=SttSetupState.INSTALLED, runtime=EngineRuntime.FAILED)

        assert broken.state is SttSetupState.INSTALLED
        assert boot_phase(snapshot(stt=broken), prompting=False) is BootPhase.BLOCKED

    def test_no_speech_model_blocks_startup(self) -> None:
        """★ **Reversed by ADR-034.** A Lumi that cannot hear is one nobody can talk to."""
        missing = SttSetup(state=SttSetupState.NOT_CONFIGURED)
        assert boot_phase(snapshot(stt=missing), prompting=False) is BootPhase.BLOCKED

    def test_the_llm_blocks_startup_too(self) -> None:
        """★ **Reversed by ADR-034.** Lumi fetches neither Ollama nor its model, but a
        reply that never comes is as broken-looking as never being heard.
        """
        for state in (
            LlmSetupState.UNKNOWN,
            LlmSetupState.NOT_CONFIGURED,
            LlmSetupState.MODEL_MISSING,
        ):
            blocked = snapshot(llm=LlmSetup(state=state, runtime=EngineRuntime.READY))
            assert boot_phase(blocked, prompting=False) is BootPhase.BLOCKED, state

        stopped = LlmSetup(state=LlmSetupState.DETECTED, runtime=EngineRuntime.STOPPED)
        assert boot_phase(snapshot(llm=stopped), prompting=False) is BootPhase.BLOCKED

    def test_a_wait_is_only_shown_when_it_can_reach_ready(self) -> None:
        """★ Regression (observed 2026-08-20): **"starting AivisSpeech…" was shown for two
        minutes after the user declined the speech model**, and setup was declared
        incomplete only once the engine finished coming up.

        The engine starting is a wait worth showing **only when it is the last thing
        missing**. With anything else already settled against it, finishing the wait
        reaches `blocked` either way — so it is said now, while the user is still there.
        """
        starting = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.STARTING)
        declined = SttSetup(state=SttSetupState.NOT_CONFIGURED)

        blocked = snapshot(tts=starting, stt=declined)
        assert boot_phase(blocked, prompting=False) is BootPhase.BLOCKED
        # The same engine state, with nothing else missing, is still a wait worth showing.
        assert boot_phase(snapshot(tts=starting), prompting=False) is BootPhase.STARTING

    def test_a_missing_llm_does_not_wait_for_the_engine_either(self) -> None:
        """Lumi neither fetches nor starts Ollama, so **there was never anything to wait for.**"""
        stopped = TtsSetup(state=TtsSetupState.INSTALLED, runtime=EngineRuntime.STOPPED)
        no_llm = LlmSetup(state=LlmSetupState.NOT_CONFIGURED)

        assert boot_phase(snapshot(tts=stopped, llm=no_llm), prompting=False) is BootPhase.BLOCKED

    @pytest.mark.parametrize("progress", [0.0, 0.2, 1.0])
    def test_a_fetch_in_progress_beats_blocked(self, progress: float) -> None:
        """**Something on its way to working shows its progress**, not "you are missing this."

        Unlike the engine starting, a fetch is **the user's own choice in progress**: hiding
        the progress bar of a download they just asked for, to show them a list instead,
        would be its own kind of lying about what is happening.
        """
        fetching = SttSetup(state=SttSetupState.INSTALLING, progress=progress)
        assert boot_phase(snapshot(stt=fetching), prompting=False) is BootPhase.INSTALLING

    def test_a_question_beats_everything(self) -> None:
        """The question is on screen, so **nothing else may claim the screen.**"""
        nothing = SetupSnapshot(
            tts=TtsSetup(state=TtsSetupState.NOT_CONFIGURED),
            llm=LlmSetup(state=LlmSetupState.NOT_CONFIGURED),
            stt=SttSetup(state=SttSetupState.NOT_CONFIGURED),
        )
        assert boot_phase(nothing, prompting=True) is BootPhase.SETUP

    def test_the_phase_is_in_the_payload(self) -> None:
        assert snapshot().to_payload(prompting=True)["boot"] == "setup"
        assert snapshot().to_payload(prompting=False)["boot"] == "ready"

    def test_every_component_appears_in_the_payload(self) -> None:
        """The Stage cannot show what it was never sent."""
        payload = SetupSnapshot().to_payload()
        assert set(payload) == {"boot", "tts", "llm", "stt"}

    def test_a_fresh_snapshot_is_blocked(self) -> None:
        """**Fail-closed.** Nothing has been detected yet, so nothing may be claimed ready."""
        assert boot_phase(SetupSnapshot(), prompting=False) is BootPhase.BLOCKED
