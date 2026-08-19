"""User settings. **roadmap open item #9 / docs/architecture/core.md.**

Every test here is about **not losing what the user meant.** The format is the least
interesting part.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lumi.settings import (
    SCHEMA_VERSION,
    InvalidSettingValue,
    SettingsUnreadable,
    Source,
    UnknownSetting,
    load,
    save,
)


def write(path: Path, raw: object) -> Path:
    file = path / "settings.json"
    file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return file


# ── Reading ──────────────────────────────────────────────


def test_no_file_yet_is_all_defaults(tmp_path: Path) -> None:
    """A first run is not a failure. **And nothing is written just for reading.**"""
    settings = load(tmp_path / "settings.json", {})

    assert settings.inference_device.source is Source.DEFAULT
    assert settings.inference_device.value == "auto"
    assert settings.tts_speed.source is Source.DEFAULT
    assert settings.tts_speed.value == "1.2"
    assert settings.locale.value == "auto"
    assert not (tmp_path / "settings.json").exists()


def test_a_stored_value_wins_over_the_default(tmp_path: Path) -> None:
    file = write(tmp_path, {"version": 1, "llm_model": "gemma3:12b"})
    settings = load(file, {})

    assert settings.llm_model.value == "gemma3:12b"
    assert settings.llm_model.source is Source.FILE


def test_the_environment_wins_over_the_file(tmp_path: Path) -> None:
    """**And says so.** A setting being overridden without saying so is worse than no
    setting at all — "I changed it and nothing happened" becomes unexplainable.
    """
    file = write(tmp_path, {"llm_model": "gemma3:12b"})
    settings = load(file, {"LUMI_LLM_MODEL": "qwen3.5:9b"})

    assert settings.llm_model.value == "qwen3.5:9b"
    assert settings.llm_model.source is Source.ENV


def test_tts_speed_accepts_a_stored_value_and_environment_override(tmp_path: Path) -> None:
    file = write(tmp_path, {"tts_speed": "1.4"})
    settings = load(file, {"LUMI_TTS_SPEED": "0.8"})

    assert settings.tts_speed.value == "0.8"
    assert settings.tts_speed.source is Source.ENV


def test_one_bad_value_costs_only_that_key(tmp_path: Path) -> None:
    """**Rejecting the file over one typo throws away every other setting.**"""
    file = write(tmp_path, {"llm_model": 42, "stt_model": "small"})
    settings = load(file, {})

    assert settings.llm_model.source is Source.DEFAULT
    assert settings.stt_model.value == "small"


def test_an_unreadable_file_falls_back_and_is_marked(tmp_path: Path) -> None:
    """**Settings can never be a reason Lumi will not start.**"""
    file = tmp_path / "settings.json"
    file.write_text("{ broken", encoding="utf-8")

    settings = load(file, {})

    assert settings.unreadable
    assert settings.llm_model.source is Source.DEFAULT


def test_a_json_array_is_treated_as_unreadable(tmp_path: Path) -> None:
    settings = load(write(tmp_path, ["nope"]), {})
    assert settings.unreadable


# ── Writing ──────────────────────────────────────────────


def test_saving_keeps_keys_this_version_does_not_know(tmp_path: Path) -> None:
    """★ **A newer Lumi's settings must survive an older one opening them.**

    Dropping unknown keys silently deletes configuration on every downgrade.
    """
    file = write(tmp_path, {"version": SCHEMA_VERSION, "from_the_future": {"a": 1}})

    save(file, load(file, {}), {"llm_model": "gemma3:12b"})

    stored = json.loads(file.read_text(encoding="utf-8"))
    assert stored["from_the_future"] == {"a": 1}
    assert stored["llm_model"] == "gemma3:12b"
    assert stored["version"] == SCHEMA_VERSION


def test_a_newer_schema_is_never_rewritten_as_this_one(tmp_path: Path) -> None:
    """★ **The version moves when a key's *meaning* changes** (`SCHEMA_VERSION`).

    So a v99 file cannot be read as v1 — and saving it back stamped `version: 1` onto
    v99 values, destroying the only record that they were not v1. The newer Lumi would
    then run its own migration over already-migrated values. **Left alone instead.**
    """
    original = {"version": 99, "llm_model": "means-something-else", "from_the_future": {"a": 1}}
    file = write(tmp_path, original)

    settings = load(file, {})

    assert settings.unreadable, "書き込みを止めるのは unreadable の経路"
    assert settings.llm_model.source is Source.DEFAULT, "v99 の値を v1 として読まない"
    with pytest.raises(SettingsUnreadable):
        save(file, settings, {"llm_model": "gemma3:12b"})
    assert json.loads(file.read_text(encoding="utf-8")) == original


def test_an_environment_override_still_applies_to_a_newer_schema(tmp_path: Path) -> None:
    """The escape hatch is an explicit act by the user, and **is never written back.**"""
    file = write(tmp_path, {"version": 99})

    settings = load(file, {"LUMI_LLM_MODEL": "gemma3:12b"})

    assert settings.llm_model.value == "gemma3:12b"
    assert settings.llm_model.source is Source.ENV


def test_an_unreadable_file_is_never_overwritten(tmp_path: Path) -> None:
    """★ **The user's own configuration is worth more than one update.**

    Saving over it destroys the only copy of what they meant, and it is still fixable
    by hand.
    """
    file = tmp_path / "settings.json"
    file.write_text("{ broken", encoding="utf-8")

    with pytest.raises(SettingsUnreadable):
        save(file, load(file, {}), {"llm_model": "x"})

    assert file.read_text(encoding="utf-8") == "{ broken"


def test_an_environment_override_is_not_written_into_the_file(tmp_path: Path) -> None:
    """**A temporary escape hatch must not silently become permanent.**"""
    file = write(tmp_path, {})
    settings = load(file, {"LUMI_LLM_MODEL": "from-env"})

    save(file, settings, {"stt_model": "small"})

    stored = json.loads(file.read_text(encoding="utf-8"))
    assert "llm_model" not in stored
    assert stored["stt_model"] == "small"


def test_a_key_that_is_not_a_setting_is_refused(tmp_path: Path) -> None:
    """**fail-closed.** Never written just because someone asked for it."""
    file = write(tmp_path, {})
    with pytest.raises(UnknownSetting, match="shell_command"):
        save(file, load(file, {}), {"shell_command": "rm -rf /"})


def test_locale_accepts_only_supported_choices(tmp_path: Path) -> None:
    file = write(tmp_path, {})
    updated = save(file, load(file, {}), {"locale": "en"})
    assert updated.locale.value == "en"

    with pytest.raises(InvalidSettingValue, match="locale"):
        save(file, updated, {"locale": "fr"})


def test_tts_speed_accepts_only_values_in_the_engine_range(tmp_path: Path) -> None:
    file = write(tmp_path, {})
    updated = save(file, load(file, {}), {"tts_speed": "1.5"})
    assert updated.tts_speed.value == "1.5"

    for value in ("0.49", "2.01", "fast", "nan", "inf"):
        with pytest.raises(InvalidSettingValue, match="tts_speed"):
            save(file, updated, {"tts_speed": value})


def test_invalid_stored_locale_falls_back_without_costing_other_keys(tmp_path: Path) -> None:
    settings = load(write(tmp_path, {"locale": "fr", "llm_model": "gemma3:12b"}), {})
    assert settings.locale.value == "auto"
    assert settings.llm_model.value == "gemma3:12b"


def test_saving_returns_the_new_effective_settings(tmp_path: Path) -> None:
    file = write(tmp_path, {})
    updated = save(file, load(file, {}), {"inference_device": "cpu"})

    assert updated.inference_device.value == "cpu"
    assert updated.inference_device.source is Source.FILE


def test_a_half_written_file_never_replaces_a_good_one(tmp_path: Path) -> None:
    """Written whole, then renamed. **A truncated file reads as corrupt next start**, and
    the never-overwrite rule would then refuse to save ever again.
    """
    file = write(tmp_path, {})
    save(file, load(file, {}), {"llm_model": "x"})

    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(file.read_text(encoding="utf-8"))["llm_model"] == "x"


def test_the_payload_carries_where_each_value_came_from(tmp_path: Path) -> None:
    """**The UI has to be able to say "this is overridden."**"""
    payload = load(write(tmp_path, {}), {"LUMI_INFERENCE_DEVICE": "cpu"}).to_payload()

    assert payload["values"]["inference_device"] == {"value": "cpu", "source": "env"}
    assert payload["version"] == SCHEMA_VERSION
