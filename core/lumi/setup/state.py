"""Setup state for the three inference components.

State machines → docs/architecture/setup.md §2 / §2b

**"Never fetched" and "tried and failed" are never mixed together.** What's asked of
the user differs.

**Installation state and process state are separate axes throughout.** Collapsing them
into one enum makes "installed but won't start" inexpressible, and hands the user the
false advice "please install it."
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
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
    """

    #: Waiting for the user's choice.
    SETUP = "setup"
    #: Fetching the engine.
    INSTALLING = "installing"
    #: Starting the engine's process.
    STARTING = "starting"
    #: **The character may be shown.**
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
    def usable(self) -> bool:
        """Whether it can speak as-is."""
        return self.state in (TtsSetupState.DETECTED, TtsSetupState.INSTALLED)


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
    def usable(self) -> bool:
        return self.state is LlmSetupState.DETECTED and self.runtime is EngineRuntime.READY


@dataclass(frozen=True, slots=True)
class SttSetup:
    """The speech-recognition model's state. **A file, not a process** — so no runtime axis."""

    state: SttSetupState = SttSetupState.UNKNOWN
    #: Which model (`small` etc.). Part of what identifies what was fetched
    model: str | None = None
    reason: str | None = None
    #: Fetch progress (0.0-1.0). Only populated for `INSTALLING`
    progress: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "model": self.model,
            "reason": self.reason,
            "progress": self.progress,
        }

    @property
    def usable(self) -> bool:
        return self.state is SttSetupState.INSTALLED


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

    **The user is only made to wait when "this is about to become usable"**
    (docs/architecture/setup.md §2b). Choosing not to fetch, and a failed fetch, both
    resolve to `READY`. Being unable to speak and Lumi not having started are different
    things — **the character is never held hostage.**

    **The LLM is deliberately absent from this.** Lumi neither fetches nor starts Ollama,
    so waiting would accomplish nothing. No LLM means a Lumi that listens and doesn't
    answer — **not a broken one.** Same for an STT model that was never fetched: only
    *fetching* waits.
    """
    if prompting:
        return BootPhase.SETUP
    if setup.tts.state is TtsSetupState.INSTALLING or setup.stt.state is SttSetupState.INSTALLING:
        return BootPhase.INSTALLING
    if setup.tts.usable and setup.tts.runtime in (EngineRuntime.STOPPED, EngineRuntime.STARTING):
        # **It just hasn't started yet — it's about to.** Marking this READY would make the
        # character flash in and then vanish (hit this in practice).
        return BootPhase.STARTING
    return BootPhase.READY


@dataclass(frozen=True, slots=True)
class SetupAnswers:
    """What's **already been asked** during first-run setup.

    Not a general-purpose settings store (the settings storage format is roadmap
    open item #9 / Phase 1). All this holds is which fetch questions have been
    **asked and answered**.

    **Tracked per component.** Answering "no" to the TTS engine says nothing about
    whether the speech-recognition model is wanted, and treating one answer as
    covering both would silently never ask the second question.
    """

    tts_prompt_answered: bool = False
    stt_prompt_answered: bool = False

    @classmethod
    def load(cls, path: Path) -> SetupAnswers:
        """Treated as "not yet asked" if unreadable (a corrupted file never blocks startup)."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(
            tts_prompt_answered=bool(raw.get("tts_prompt_answered", False)),
            stt_prompt_answered=bool(raw.get("stt_prompt_answered", False)),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tts_prompt_answered": self.tts_prompt_answered,
                    "stt_prompt_answered": self.stt_prompt_answered,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
