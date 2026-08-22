"""User settings. **Core owns them; the Stage only shows and asks** (docs/architecture/ui.md §2).

Storage format → docs/architecture/core.md "設定" / roadmap open item #9

## Why JSON and not TOML

The Content Pack is TOML because **a person writes it**. This file is different: **the
program writes it**, from the settings UI. `tomllib` is read-only, so writing TOML would
mean a new dependency purely to serialize a handful of keys — and comments (TOML's real advantage)
cannot survive a program rewriting the file anyway.

## Rules that matter more than the format

| Rule | Why |
|---|---|
| A broken file falls back to defaults and is **never overwritten** | It stays recoverable |
| A **newer schema** is treated the same way | Version unreadable; saving stamps a false version |
| Unknown keys are **preserved** on save | A downgrade must not lose them |
| One bad value **only costs that key** | One typo must not discard every other setting |
| Environment overrides the file, **visibly** | "I changed it and nothing happened" otherwise |

## Retention is a setting, and that is the point

docs/contracts/privacy.md §4 keeps a deadline by default and lets the user remove it.
**Both halves matter**: without a default, nobody ever deletes anything; without the
choice, "we decided how long you may keep your own conversations" is not the user's
machine. `unlimited` is spelled out rather than encoded as a number — see `UNLIMITED`.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

#: Bumped when a key's meaning changes, not when one is added. **Adding is already safe**
#: (a missing key takes its default)
SCHEMA_VERSION: Final = 1


class Source(StrEnum):
    """Where an effective value came from. **Shown in the UI**, because a setting that is
    being overridden and does not say so is worse than no setting at all.
    """

    DEFAULT = "default"
    FILE = "file"
    ENV = "env"


@dataclass(frozen=True, slots=True)
class Setting:
    """One effective value and its origin."""

    value: str
    source: Source

    def to_payload(self) -> dict[str, Any]:
        return {"value": self.value, "source": str(self.source)}


@dataclass(frozen=True, slots=True)
class Settings:
    """The effective settings. **Immutable** — changing one produces a new object."""

    inference_device: Setting
    llm_model: Setting
    stt_model: Setting
    locale: Setting
    tts_speed: Setting
    #: How long the conversation log, the Kernel's events and the audit log are kept, in
    #: days — or `unlimited`. **Defaults are deadlines** (docs/contracts/privacy.md §4)
    retention_episodes: Setting
    retention_events: Setting
    retention_audit: Setting
    #: Keys this version does not know about. **Kept so a downgrade does not lose them**
    unknown: Mapping[str, Any]
    #: `True` when the file existed but could not be read. **Never overwrite it**
    unreadable: bool = False

    def get(self, key: str) -> Setting:
        """One effective value by key. **The field names are the keys** in `KEYS`."""
        return cast(Setting, getattr(self, key))

    def to_payload(self) -> dict[str, Any]:
        # **Built from `KEYS`, never from a second list.** A key added to one list and not
        # the other is a setting the user can change and never see — or, worse, one that
        # saves and silently reverts on the next start.
        return {
            "version": SCHEMA_VERSION,
            "unreadable": self.unreadable,
            "values": {key: self.get(key).to_payload() for key in KEYS},
        }


#: key → (env var, default). **The single list of what is configurable**
KEYS: Final[dict[str, tuple[str, str]]] = {
    "inference_device": ("LUMI_INFERENCE_DEVICE", "auto"),
    "llm_model": ("LUMI_LLM_MODEL", "qwen3.5:9b"),
    "stt_model": ("LUMI_STT_MODEL", "large-v3-turbo"),
    "locale": ("LUMI_LOCALE", "auto"),
    "tts_speed": ("LUMI_TTS_SPEED", "1.2"),
    # Days, or `unlimited`. **The numbers come from docs/contracts/privacy.md §2**, which
    # is where a change to them belongs first.
    "retention_episodes": ("LUMI_RETENTION_EPISODES", "90"),
    "retention_events": ("LUMI_RETENTION_EVENTS", "30"),
    "retention_audit": ("LUMI_RETENTION_AUDIT", "180"),
}

#: What the user picks when they want no deadline at all. **Spelled out rather than
#: encoded as 0 or -1**: "0 days" is a real answer meaning "keep nothing", and a setting
#: where one of those silently means the opposite of the other is a setting nobody can read.
UNLIMITED: Final = "unlimited"

#: Keys holding a retention period.
RETENTION_KEYS: Final = ("retention_episodes", "retention_events", "retention_audit")

#: A century. **Not a policy, a guard**: `timedelta` refuses much larger numbers, and a
#: deadline nobody alive will see is what `unlimited` is for.
MAX_RETENTION_DAYS: Final = 36_500

TTS_SPEED_MIN: Final = 0.5
TTS_SPEED_MAX: Final = 2.0

#: Closed choices are validated by Core. Free-form model names intentionally are not.
VALID_VALUES: Final[dict[str, frozenset[str]]] = {
    "inference_device": frozenset({"auto", "cuda", "cpu"}),
    "locale": frozenset({"auto", "ja", "en"}),
}


def _is_valid(key: str, value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if key in RETENTION_KEYS:
        if value == UNLIMITED:
            return True
        # `isdigit()` alone accepts "１２３" and "٣", which `int()` then happily parses —
        # a retention period in Arabic-Indic digits is a typo, not a preference.
        if not (value.isascii() and value.isdigit()):
            return False
        # **An upper bound, because the number becomes a `timedelta`.** Past a century the
        # honest answer is `unlimited`, and far past it the arithmetic raises instead
        return int(value) <= MAX_RETENTION_DAYS
    if key == "tts_speed":
        try:
            speed = float(value)
        except ValueError:
            return False
        return math.isfinite(speed) and TTS_SPEED_MIN <= speed <= TTS_SPEED_MAX
    allowed = VALID_VALUES.get(key)
    return allowed is None or value in allowed


def _resolve(key: str, stored: Mapping[str, Any], env: Mapping[str, str]) -> Setting:
    """Environment beats file beats default. **The origin travels with the value.**"""
    variable, fallback = KEYS[key]
    from_env = env.get(variable)
    if from_env and _is_valid(key, from_env):
        return Setting(value=from_env, source=Source.ENV)
    value = stored.get(key)
    if isinstance(value, str) and _is_valid(key, value):
        return Setting(value=value, source=Source.FILE)
    if value is not None:
        # **One bad value costs only that key.** Never throws away the rest of the file
        log.warning("settings.invalid_value", key=key, value=repr(value))
    return Setting(value=fallback, source=Source.DEFAULT)


def _is_newer_schema(stored: Mapping[str, Any]) -> bool:
    """Whether the file claims a schema this version does not know.

    A missing or non-integer `version` is **not** treated as newer — a hand-written file
    is a normal thing to find, and refusing to save one would be a worse failure than
    reading it optimistically.
    """
    version = stored.get("version")
    return isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION


def load(path: Path, env: Mapping[str, str] | None = None) -> Settings:
    """Reads the file. **Never raises** — settings can't be a reason Lumi won't start."""
    environ = env if env is not None else os.environ
    stored: dict[str, Any] = {}
    unreadable = False

    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            # **Loud, and left alone.** Saving over it would destroy the only copy
            log.warning("settings.unreadable", path=str(path), error=str(error))
            unreadable = True
        else:
            if isinstance(raw, dict):
                stored = raw
            else:
                log.warning("settings.not_an_object", path=str(path))
                unreadable = True

    if _is_newer_schema(stored):
        # **A newer Lumi wrote this.** `SCHEMA_VERSION` moves when a key's *meaning* changes,
        # so these values cannot be read as this schema — and `save` would stamp them as this
        # one, destroying the only record that they were not. Same treatment as unreadable:
        # defaults, and the file is left alone. (An environment override still applies; it is
        # an explicit act by the user and is never written back.)
        log.warning(
            "settings.schema_too_new",
            path=str(path),
            version=stored.get("version"),
            known=SCHEMA_VERSION,
        )
        stored = {}
        unreadable = True

    known = {*KEYS, "version"}
    return Settings(
        inference_device=_resolve("inference_device", stored, environ),
        llm_model=_resolve("llm_model", stored, environ),
        stt_model=_resolve("stt_model", stored, environ),
        locale=_resolve("locale", stored, environ),
        tts_speed=_resolve("tts_speed", stored, environ),
        retention_episodes=_resolve("retention_episodes", stored, environ),
        retention_events=_resolve("retention_events", stored, environ),
        retention_audit=_resolve("retention_audit", stored, environ),
        unknown={key: value for key, value in stored.items() if key not in known},
        unreadable=unreadable,
    )


def save(path: Path, settings: Settings, changes: Mapping[str, str]) -> Settings:
    """Writes the changed keys and returns the new effective settings.

    **Refuses to write over a file it could not read** — the user's own configuration is
    worth more than one update, and it is still recoverable by hand.

    **Environment overrides are not written.** Storing the override would silently make a
    temporary escape hatch permanent.
    """
    if settings.unreadable:
        raise SettingsUnreadable(f"Cannot read {path.name}; will not overwrite")

    for key, value in changes.items():
        if key not in KEYS:
            raise UnknownSetting(f"Unknown setting: {key}")
        if not _is_valid(key, value):
            raise InvalidSettingValue(f"Invalid value for {key}: {value!r}")

    stored: dict[str, Any] = {"version": SCHEMA_VERSION, **settings.unknown}
    # **Every key in `KEYS`.** A hand-maintained list here once left the retention
    # settings out: they validated, they were accepted, and they vanished on restart.
    for key in KEYS:
        current = settings.get(key)
        value = changes.get(key, current.value)
        if key in changes or current.source is Source.FILE:
            stored[key] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    # **Written whole, then renamed.** A half-written settings file reads as corrupt on the
    # next start, and the rule above would then refuse to save ever again
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    log.info("settings.saved", path=str(path), changed=sorted(changes))
    return load(path)


class SettingsError(RuntimeError):
    """Base for settings failures."""


class SettingsUnreadable(SettingsError):
    """The existing file could not be read. **Never overwritten.**"""


class UnknownSetting(SettingsError):
    """A key that is not a setting.

    **fail-closed** — never written just because someone asked for it.
    """


class InvalidSettingValue(SettingsError):
    """A closed-choice setting was given a value outside its allowlist."""
