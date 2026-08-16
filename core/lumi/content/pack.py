"""Reads Content Packs. **Data only. Never accepts code.**

Spec → docs/architecture/extension.md §9 / Credit obligations → docs/licensing.md §6

## Two things that fail closed

| Condition | Reason |
|---|---|
| **Contains code** | Easily shared and redistributed. Including code makes it the same threat as an Extension |
| **`[credit]` is missing** | Credit attribution is a licensing obligation from the voice source's terms. **Not something that can be "added later."** |

**Core never interprets `credit_text`.** The wording the license requires is decided by
the rights holder, and reformatting it could make it fail to satisfy that requirement.
**It's passed to the Stage unchanged.**
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

#: **If even one of these is present, loading is refused.** Judging by extension isn't
#: airtight, but it's enough to catch "accidentally included," and it errs on the
#: fail-closed side.
CODE_SUFFIXES: Final = frozenset(
    {
        ".py",
        ".pyc",
        ".pyd",
        ".js",
        ".mjs",
        ".ts",
        ".wasm",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bat",
        ".cmd",
        ".ps1",
        ".sh",
        ".lua",
        ".vbs",
        ".jar",
    }
)


class ContentPackError(RuntimeError):
    """Couldn't be loaded. **Never silently substitutes a default.**"""


@dataclass(frozen=True, slots=True)
class Credit:
    """The wording the license requires. **Core doesn't interpret it — passed through as-is.**"""

    name: str
    credit_text: str
    license_name: str
    license_file: str = ""
    license_url: str = ""


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    #: `None` = uses the engine's default speaker (stays default in Phase 1)
    speaker: int | None
    credit: Credit


@dataclass(frozen=True, slots=True)
class CharacterPack:
    root: Path
    name: str
    #: Persona prompt. **Becomes PromptAssembly's persona** (trusted)
    persona: str
    voice: VoiceSettings


def load_character(root: Path) -> CharacterPack:
    """Reads `character.toml` and `voice.toml`. **Raises if anything is missing.**"""
    if not root.is_dir():
        raise ContentPackError(f"Content Pack が無い: {root}")

    _reject_code(root)

    character = _read_toml(root / "character.toml")
    voice = _read_toml(root / "voice.toml")

    section = _table(character, "character", root)
    name = _text(section, "name", root / "character.toml")
    persona = _text(section, "persona", root / "character.toml")

    pack = CharacterPack(
        root=root,
        name=name,
        persona=persona,
        voice=VoiceSettings(
            speaker=_optional_int(_table(voice, "voice", root).get("speaker")),
            credit=_credit(_table(voice, "credit", root), root / "voice.toml"),
        ),
    )
    log.info("content.loaded", name=pack.name, root=str(root))
    return pack


def _reject_code(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in CODE_SUFFIXES:
            raise ContentPackError(f"Content Pack にコードが含まれている: {path.name}")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContentPackError(f"必須のファイルが無い: {path}")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ContentPackError(f"{path.name} が読めない: {error}") from error


def _table(data: dict[str, Any], key: str, root: Path) -> dict[str, Any]:
    section = data.get(key)
    if not isinstance(section, dict):
        raise ContentPackError(f"{root.name}: [{key}] が無い")
    return section


def _text(section: dict[str, Any], key: str, path: Path) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContentPackError(f"{path.name}: {key} が無い")
    return value.strip()


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _credit(section: dict[str, Any], path: Path) -> Credit:
    """**Refuses to load if missing.** Credit is an obligation that can't be added after the fact."""
    return Credit(
        name=_text(section, "name", path),
        credit_text=_text(section, "credit_text", path),
        license_name=_text(section, "license_name", path),
        license_file=str(section.get("license_file", "")),
        license_url=str(section.get("license_url", "")),
    )
