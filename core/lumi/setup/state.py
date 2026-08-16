"""TTS setup state. The state machine is defined in docs/architecture/setup.md §2.

**"Never fetched" and "tried and failed" are never mixed together.** What's asked of
the user differs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
    #: Starting up. Not yet responding (the first run can take minutes as the engine fetches its own model).
    STARTING = "starting"
    #: Can speak.
    READY = "ready"
    #: Installed but won't start = **broken**.
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
    #: The reason for the failure. Only populated for `FAILED`. **The wording that prevents silent degradation.**
    reason: str | None = None
    #: Fetch progress (0.0-1.0). Only populated for `INSTALLING`.
    progress: float | None = None
    #: The state of the engine **process**. Moves independently of installation state.
    runtime: EngineRuntime = EngineRuntime.STOPPED

    def to_payload(self, *, prompting: bool = False) -> dict[str, Any]:
        return {
            "boot": str(boot_phase(self, prompting=prompting)),
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


def boot_phase(setup: TtsSetup, *, prompting: bool) -> BootPhase:
    """Decides the boot phase. **A pure function** (docs/architecture/ui.md).

    **The user is only made to wait when "this is about to become usable."**
    Choosing not to fetch, and a failed fetch, both resolve to `READY`. Being unable
    to speak and Lumi not having started are different things — **the character is
    never held hostage.**
    """
    if prompting:
        return BootPhase.SETUP
    if setup.state is TtsSetupState.INSTALLING:
        return BootPhase.INSTALLING
    if setup.usable and setup.runtime in (EngineRuntime.STOPPED, EngineRuntime.STARTING):
        # **It just hasn't started yet — it's about to.**
        # Marking this READY would make the character flash in and then vanish (hit this in practice).
        return BootPhase.STARTING
    return BootPhase.READY


@dataclass(frozen=True, slots=True)
class SetupAnswers:
    """What's **already been asked** during first-run setup.

    Not a general-purpose settings store (the settings storage format is roadmap
    open item #9 / Phase 1). All this holds is whether "the TTS-fetch question was
    asked and answered."
    """

    tts_prompt_answered: bool = False

    @classmethod
    def load(cls, path: Path) -> SetupAnswers:
        """Treated as "not yet asked" if unreadable (a corrupted file never blocks startup)."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(tts_prompt_answered=bool(raw.get("tts_prompt_answered", False)))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tts_prompt_answered": self.tts_prompt_answered}, ensure_ascii=False),
            encoding="utf-8",
        )
