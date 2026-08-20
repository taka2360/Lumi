"""Setup state for the three inference components.

State machines → docs/architecture/setup.md §2 / §2b

**"Never fetched" and "tried and failed" are never mixed together.** What's asked of
the user differs.

**Installation state and process state are separate axes throughout.** Collapsing them
into one enum makes "installed but won't start" inexpressible, and hands the user the
false advice "please install it."

**Nothing here remembers what the user answered.** Setup is a precondition for Lumi
running at all (ADR-034), so an answer of "not now" is not something to carry forward —
remembering it would mean never asking again about something Lumi cannot start without.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TtsSetupState(StrEnum):
    #: Not checked yet.
    UNKNOWN = "unknown"
    #: No usable engine, and the user hasn't yet chosen to fetch one.
    NOT_CONFIGURED = "not_configured"
    #: Found an engine the user installed separately.
    DETECTED = "detected"
    #: Currently fetching.
    INSTALLING = "installing"
    #: An engine Lumi installed is usable.
    INSTALLED = "installed"
    #: Attempted to fetch and failed. **Never reverts to not-configured.**
    FAILED = "failed"


class EngineRuntime(StrEnum):
    """The state of the engine **process**. A separate axis from the installation
    state (`TtsSetupState`).

    docs/architecture/setup.md "Never mix installation state and process state"

    Mixing these into one enum would make it impossible to express "installed but
    can't start," and would tell the user the false "please fetch it" guidance.
    """

    #: Not running.
    STOPPED = "stopped"
    # : Starting up. Not yet responding (the first run can take minutes as the engine fetches its
    # own model).
    STARTING = "starting"
    #: Can speak.
    READY = "ready"
    #: Installed but won't start = **broken**.
    FAILED = "failed"


class LlmSetupState(StrEnum):
    """Whether Ollama is present. **Lumi never fetches or installs it** (ADR-023).

    docs/architecture/setup.md §2b
    """

    #: Not checked yet.
    UNKNOWN = "unknown"
    #: **Ollama isn't there.** Point at the official site
    NOT_CONFIGURED = "not_configured"
    #: Ollama is there.
    DETECTED = "detected"
    #: **Ollama is there but the configured model isn't.** Suggest `ollama pull <model>`
    MODEL_MISSING = "model_missing"


class SttSetupState(StrEnum):
    """Whether the speech-recognition model has been fetched. **Same shape as TTS**, minus
    `detected`: there is no such thing as an STT model the user installed separately
    (docs/architecture/setup.md §2b).
    """

    #: Not checked yet.
    UNKNOWN = "unknown"
    #: Not fetched, and the user hasn't yet chosen to.
    NOT_CONFIGURED = "not_configured"
    #: Currently fetching.
    INSTALLING = "installing"
    #: Usable.
    INSTALLED = "installed"
    #: Attempted and failed. **Never reverts to not-configured.**
    FAILED = "failed"


class BootPhase(StrEnum):
    """How far startup has progressed. **Core decides when it's okay to show the character.**

    Defined in → docs/architecture/ui.md "Boot phases"

    A character that's standing there but unresponsive **looks broken.** While the
    engine takes minutes to fetch and a dozen-odd seconds to start, something
    showing what's actually happening is displayed instead.

    **The same reasoning applies far harder to a missing component** (ADR-034): a dozen
    seconds of starting is nothing next to a reply that is never coming. So the character
    waits for all three, and `BLOCKED` says what is still missing.
    """

    #: Waiting for the user's choice.
    SETUP = "setup"
    #: Fetching the engine.
    INSTALLING = "installing"
    #: Starting the engine's process.
    STARTING = "starting"
    #: **Setup is not finished.** Something the conversation needs is missing, so the
    #: character is not shown. What is missing and how to fix it is displayed instead
    #: (ADR-034).
    BLOCKED = "blocked"
    #: **The character may be shown.** All three of STT / LLM / TTS are usable.
    READY = "ready"


@dataclass(frozen=True, slots=True)
class TtsSetup:
    """The state distributed to the Stage. The Stage **only displays it.**"""

    state: TtsSetupState
    engine_name: str | None = None
    version: str | None = None
    port: int | None = None
    executable: str | None = None
    # : The reason for the failure. Only populated for `FAILED`. **The wording that prevents silent
    # degradation.**
    reason: str | None = None
    #: Fetch progress (0.0-1.0). Only populated for `INSTALLING`.
    progress: float | None = None
    #: The state of the engine **process**. Moves independently of installation state.
    runtime: EngineRuntime = EngineRuntime.STOPPED

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "engine_name": self.engine_name,
            "version": self.version,
            "port": self.port,
            "executable": self.executable,
            "reason": self.reason,
            "progress": self.progress,
            "runtime": str(self.runtime),
        }

    @property
    def installed(self) -> bool:
        """Whether an engine is **on the machine** — nothing about whether it runs.

        This is what decides whether a Provider gets registered at all. **Not the same
        question as `ready`**: an engine that is installed and refuses to start is
        exactly the case a single flag would erase.
        """
        return self.state in (TtsSetupState.DETECTED, TtsSetupState.INSTALLED)

    @property
    def ready(self) -> bool:
        """Whether Lumi **can speak right now** (ADR-034). Both axes have to agree."""
        return self.installed and self.runtime is EngineRuntime.READY


@dataclass(frozen=True, slots=True)
class LlmSetup:
    """The LLM's state. **Lumi neither fetches nor starts it** (ADR-023), so there is no
    progress and no `starting`.
    """

    state: LlmSetupState = LlmSetupState.UNKNOWN
    #: The model that was looked for. **Named so `model_missing` can say which one**
    model: str | None = None
    #: What to tell the user. Populated for `not_configured` / `model_missing`
    reason: str | None = None
    #: Whether the API on 127.0.0.1 answers. `STARTING` never occurs (nothing starts it)
    runtime: EngineRuntime = EngineRuntime.STOPPED

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "model": self.model,
            "reason": self.reason,
            "runtime": str(self.runtime),
        }

    @property
    def ready(self) -> bool:
        """Whether Lumi **can answer right now** (ADR-034).

        `MODEL_MISSING` is deliberately not ready: Ollama answering is not the same as
        Ollama being able to run the model Lumi was configured with.
        """
        return self.state is LlmSetupState.DETECTED and self.runtime is EngineRuntime.READY


@dataclass(frozen=True, slots=True)
class SttSetup:
    """The speech-recognition model's installation and Provider runtime state.

    A complete set of files is not the same as a model CTranslate2 can load. Keeping the
    axes separate prevents a CUDA / compute-type / corrupt-weight failure from being
    reported as a failed download (ADR-035).
    """

    state: SttSetupState = SttSetupState.UNKNOWN
    #: Which model (`small` etc.). Part of what identifies what was fetched
    model: str | None = None
    reason: str | None = None
    #: Fetch progress (0.0-1.0). Only populated for `INSTALLING`
    progress: float | None = None
    #: Whether the Provider can transcribe. Separate from whether its files are installed.
    runtime: EngineRuntime = EngineRuntime.STOPPED

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "model": self.model,
            "reason": self.reason,
            "progress": self.progress,
            "runtime": str(self.runtime),
        }

    @property
    def ready(self) -> bool:
        """Whether Lumi **can hear right now** (ADR-034 / ADR-035)."""
        return self.state is SttSetupState.INSTALLED and self.runtime is EngineRuntime.READY


@dataclass(frozen=True, slots=True)
class SetupSnapshot:
    """All three components, as broadcast to the Stage. **The Stage only displays it.**

    Held as one object because **the boot phase is a function of all three** and
    broadcasting from several places could not guarantee ordering.
    """

    tts: TtsSetup = field(default_factory=lambda: TtsSetup(state=TtsSetupState.UNKNOWN))
    llm: LlmSetup = field(default_factory=LlmSetup)
    stt: SttSetup = field(default_factory=SttSetup)

    def to_payload(self, *, prompting: bool = False) -> dict[str, Any]:
        return {
            "boot": str(boot_phase(self, prompting=prompting)),
            "tts": self.tts.to_payload(),
            "llm": self.llm.to_payload(),
            "stt": self.stt.to_payload(),
        }


def boot_phase(setup: SetupSnapshot, *, prompting: bool) -> BootPhase:
    """Decides the boot phase. **A pure function** (docs/architecture/ui.md).

    **The character does not come out until all three of STT / LLM / TTS are usable**
    (ADR-034). Hearing, thinking and speaking are what a conversation is made of, and a
    character standing on the desktop with any one of them missing reads as broken rather
    than as unfinished — the loading screen's own reasoning, applied to a wait that never
    ends.

    The three loading-ish phases are separated from `BLOCKED`, because **only they are
    actually waiting on Lumi**:

    | phase | waiting on |
    |---|---|
    | `SETUP` | the user's answer to a question on screen |
    | `INSTALLING` | a fetch with progress |
    | `STARTING` | a Provider becoming usable (TTS process start / STT model load) |
    | `BLOCKED` | **the user, outside Lumi** (install Ollama, retry, or give up) |

    **A wait is only worth showing when finishing it would actually reach `READY`.** So
    anything already settled against us is answered first: with the speech model declined,
    the engine coming up changes nothing, and "starting AivisSpeech…" is a loading screen
    for an outcome that cannot happen. Declining a component and then being made to wait
    two minutes to be told setup is incomplete is the exact opposite of what the phase is
    for (observed 2026-08-20).

    `INSTALLING` stays ahead of it, because a fetch is **the user's own choice in
    progress** — hiding the progress bar of a download they just asked for, to show them a
    list, would be its own kind of lying about what is happening.
    """
    if prompting:
        return BootPhase.SETUP
    if setup.tts.state is TtsSetupState.INSTALLING or setup.stt.state is SttSetupState.INSTALLING:
        return BootPhase.INSTALLING
    if not setup.llm.ready:
        # Lumi neither installs nor starts Ollama, so waiting cannot change this state.
        return BootPhase.BLOCKED
    if not setup.tts.installed or setup.stt.state is not SttSetupState.INSTALLED:
        # Missing / failed acquisition is settled against us. A runtime warmup cannot fix it.
        return BootPhase.BLOCKED
    if setup.tts.runtime is EngineRuntime.FAILED or setup.stt.runtime is EngineRuntime.FAILED:
        # Installed but unusable. **Never rewrite this as an acquisition failure** (ADR-035).
        return BootPhase.BLOCKED
    if setup.tts.runtime in (
        EngineRuntime.STOPPED,
        EngineRuntime.STARTING,
    ) or setup.stt.runtime in (EngineRuntime.STOPPED, EngineRuntime.STARTING):
        # Both are installed and Core is about to make them usable. This wait can reach READY.
        return BootPhase.STARTING
    if not (setup.tts.ready and setup.stt.ready):
        # Fail closed if a future runtime value reaches here without being explicitly handled.
        return BootPhase.BLOCKED
    return BootPhase.READY
