"""Reads Content Packs. **Data only. Never accepts code.**

Spec → docs/architecture/extension.md §9 / Credit obligations → docs/licensing.md §6

## Two things that fail closed

| Condition | Reason |
|---|---|
| **Contains code** | Shared and redistributed easily — the same threat as an Extension |
| **`[credit]` is missing** | A licensing obligation from the voice terms — never "added later." |

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


#: Default volume scale
DEFAULT_VOLUME: Final = 0.4


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

    def to_payload(self) -> dict[str, str]:
        """**Passed through verbatim.** The wording a license demands is the rights holder's
        to choose; reformatting it can stop satisfying the requirement
        (docs/architecture/extension.md §9).
        """
        return {
            "name": self.name,
            "credit_text": self.credit_text,
            "license_name": self.license_name,
            "license_url": self.license_url,
        }


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    #: `None` = uses the engine's default speaker (stays default in Phase 1)
    speaker: int | None
    credit: Credit
    volume: float = DEFAULT_VOLUME


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """What the character looks like. **The Content Pack chooses the model**
    (docs/architecture/extension.md §9) — hardcoding one in Core would make the credit a lie
    the moment it is swapped.
    """

    #: Absolute path to the model file. **Resolved at load**, so a pack that declares a model
    #: it doesn't ship fails here rather than as a blank character later
    path: Path
    #: `vrm0` / `vrm1`. **Read but not acted on in Phase 1** — `@pixiv/three-vrm` handles both,
    #: and Live2D is Phase 9 (docs/architecture/ui.md §3)
    format: str
    credit: Credit

    def to_payload(self) -> dict[str, object]:
        """What the Stage needs to draw it, and what the credits screen needs to say.

        **An absolute path, not a URL.** Turning it into something the WebView can fetch is
        Shell's job — Core neither serves files nor knows how Shell addresses them (ADR-029).
        """
        return {
            "path": str(self.path),
            "format": self.format,
            "credit": self.credit.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class CharacterPack:
    root: Path
    name: str
    #: Persona prompt. **Becomes PromptAssembly's persona** (trusted)
    persona: str
    voice: VoiceSettings
    #: `None` = no model declared. **The placeholder runs, and the Stage says so**
    #: (docs/architecture/ui.md). A voice-only pack is a legitimate pack
    model: ModelSettings | None = None


def load_character(root: Path) -> CharacterPack:
    """Reads `character.toml` and `voice.toml`. **Raises if anything is missing.**"""
    if not root.is_dir():
        raise ContentPackError(f"Content Pack not found: {root}")

    _reject_code(root)

    character = _read_toml(root / "character.toml")
    voice = _read_toml(root / "voice.toml")

    section = _table(character, "character", root)
    name = _text(section, "name", root / "character.toml")
    persona = _text(section, "persona", root / "character.toml")
    voice_section = _table(voice, "voice", root)

    pack = CharacterPack(
        root=root,
        name=name,
        persona=persona,
        voice=VoiceSettings(
            speaker=_optional_int(voice_section.get("speaker")),
            credit=_credit(_table(voice, "credit", root), root / "voice.toml"),
            volume=_parse_volume(voice_section.get("volume"), root / "voice.toml"),
        ),
        # **`[model]` はトップレベル**（`[character]` の中ではない）。voice.toml の
        # `[voice]` / `[credit]` と同じ並び
        model=_model(character, root),
    )
    log.info("content.loaded", name=pack.name, root=str(root))
    return pack


def _model(document: dict[str, Any], root: Path) -> ModelSettings | None:
    """Reads `[model]`. **Absent is fine; incomplete is not.**

    Declaring a model and shipping no credit is the same failure `[credit]` in `voice.toml`
    guards against: **crediting is not something that can be added after distribution**
    (docs/architecture/extension.md §9). Whether a given license *requires* the credit is a
    separate question from whether Lumi shows it — Lumi shows it either way.
    """
    model = document.get("model")
    if model is None:
        return None
    if not isinstance(model, dict):
        raise ContentPackError("character.toml [model] is not a table")

    path = _inside(root, _text(model, "file", root / "character.toml"))
    if not path.is_file():
        # **Never fall back silently.** A pack that names a model it doesn't ship is broken,
        # and a blank character is the least informative way to report it
        raise ContentPackError(f"File specified by [model] does not exist: {path}")

    return ModelSettings(
        path=path,
        format=str(model.get("format", "")),
        credit=_credit(_table(model, "credit", root), root / "character.toml"),
    )


def _inside(root: Path, declared: str) -> Path:
    """Resolves a path the pack declared, and **refuses anything outside the pack.**

    A Content Pack is redistributed data, not code, and `root / declared` alone honours an
    absolute path (`Path("a") / "C:/x"` is `C:/x`) and `..`. The resolved path is then
    published to the Stage (ADR-029), so **a pack could name any readable local file**.
    Shell's asset scope would refuse to serve it, but a boundary that only holds because
    the far side also checks is not a boundary.
    """
    path = (root / declared).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ContentPackError(f"Points outside Content Pack: {declared}") from error
    return path


def _reject_code(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in CODE_SUFFIXES:
            raise ContentPackError(f"Content Pack contains code: {path.name}")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ContentPackError(f"Required file missing: {path}")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ContentPackError(f"Cannot read {path.name}: {error}") from error


def _table(data: dict[str, Any], key: str, root: Path) -> dict[str, Any]:
    section = data.get(key)
    if not isinstance(section, dict):
        raise ContentPackError(f"{root.name}: missing [{key}]")
    return section


def _text(section: dict[str, Any], key: str, path: Path) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContentPackError(f"{path.name}: missing {key}")
    return value.strip()


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _credit(section: dict[str, Any], path: Path) -> Credit:
    """**Refuses to load if missing.** Credit is an obligation that can't be added later."""
    return Credit(
        name=_text(section, "name", path),
        credit_text=_text(section, "credit_text", path),
        license_name=_text(section, "license_name", path),
        license_file=str(section.get("license_file", "")),
        license_url=str(section.get("license_url", "")),
    )


def _parse_volume(value: Any, path: Path) -> float:
    # Fall back to default when omitted
    if value is None:
        return DEFAULT_VOLUME
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        val = float(value)
        if 0.0 <= val <= 2.0:
            return val
        raise ContentPackError(
            f"{path.name}: volume must be between 0.0 and 2.0 (got: {value})"
        )
    raise ContentPackError(f"{path.name}: volume must be a number (got: {value})")
