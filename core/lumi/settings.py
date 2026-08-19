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
| Unknown keys are **preserved** on save | A downgrade must not lose them |
| One bad value **only costs that key** | One typo must not discard every other setting |
| Environment overrides the file, **visibly** | "I changed it and nothing happened" otherwise |
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

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
    #: Keys this version does not know about. **Kept so a downgrade does not lose them**
    unknown: Mapping[str, Any]
    #: `True` when the file existed but could not be read. **Never overwrite it**
    unreadable: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "unreadable": self.unreadable,
            "values": {
                "inference_device": self.inference_device.to_payload(),
                "llm_model": self.llm_model.to_payload(),
                "stt_model": self.stt_model.to_payload(),
                "locale": self.locale.to_payload(),
            },
        }


#: key → (env var, default). **The single list of what is configurable**
KEYS: Final[dict[str, tuple[str, str]]] = {
    "inference_device": ("LUMI_INFERENCE_DEVICE", "auto"),
    "llm_model": ("LUMI_LLM_MODEL", "qwen3.5:9b"),
    "stt_model": ("LUMI_STT_MODEL", "large-v3-turbo"),
    "locale": ("LUMI_LOCALE", "auto"),
}

#: Closed choices are validated by Core. Free-form model names intentionally are not.
VALID_VALUES: Final[dict[str, frozenset[str]]] = {
    "inference_device": frozenset({"auto", "cuda", "cpu"}),
    "locale": frozenset({"auto", "ja", "en"}),
}


def _is_valid(key: str, value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
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


def load(path: Path, env: Mapping[str, str] | None = None) -> Settings:
    """Reads the file. **Never raises** — settings can't be a reason Lumi won't start."""
    environ = env if env is not None else os.environ
    stored: dict[str, Any] = {}
    unreadable = False

    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            # **Loud, and left alone.** Saving over it would destroy the only copy
            log.warning("settings.unreadable", path=str(path), error=str(error))
            unreadable = True
        else:
            if isinstance(raw, dict):
                stored = raw
            else:
                log.warning("settings.not_an_object", path=str(path))
                unreadable = True

    known = {*KEYS, "version"}
    return Settings(
        inference_device=_resolve("inference_device", stored, environ),
        llm_model=_resolve("llm_model", stored, environ),
        stt_model=_resolve("stt_model", stored, environ),
        locale=_resolve("locale", stored, environ),
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
        raise SettingsUnreadable(f"{path.name} を読めなかったので上書きしない")

    for key, value in changes.items():
        if key not in KEYS:
            raise UnknownSetting(f"{key} は設定項目ではない")
        if not _is_valid(key, value):
            raise InvalidSettingValue(f"{key} の値が不正: {value!r}")

    stored: dict[str, Any] = {"version": SCHEMA_VERSION, **settings.unknown}
    for key, current in (
        ("inference_device", settings.inference_device),
        ("llm_model", settings.llm_model),
        ("stt_model", settings.stt_model),
        ("locale", settings.locale),
    ):
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
