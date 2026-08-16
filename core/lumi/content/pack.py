"""Content Pack を読む。**データだけ。コードは受け付けない。**

仕様 → docs/architecture/extension.md §9 / クレジット義務 → docs/licensing.md §6

## fail-closed で落とす2つ

| 条件 | 理由 |
|---|---|
| **コードが入っている** | 共有・配布されやすい。コードを含むと Extension と同じ脅威になる |
| **`[credit]` が無い** | クレジット表記は音源規約上の義務。**「後で足す」ができない性質のもの** |

**Core は `credit_text` を解釈しない。** 規約が要求する表記は権利者が決めるので、
整形すると要求を満たさなくなりうる。**そのまま Stage に渡す。**
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

#: **これが1つでも入っていたら読み込まない。** 拡張子で判断するのは完全ではないが、
#: 「うっかり入った」を止めるには十分であり、fail-closed の側に倒れている
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
    """読み込めない。**黙って既定値で代用しない。**"""


@dataclass(frozen=True, slots=True)
class Credit:
    """規約が要求する表記。**Core は解釈せず、そのまま渡す。**"""

    name: str
    credit_text: str
    license_name: str
    license_file: str = ""
    license_url: str = ""


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    #: `None` = エンジンの既定話者を使う（Phase 1 は既定のまま）
    speaker: int | None
    credit: Credit


@dataclass(frozen=True, slots=True)
class CharacterPack:
    root: Path
    name: str
    #: 人格プロンプト。**PromptAssembly の persona になる**（trusted）
    persona: str
    voice: VoiceSettings


def load_character(root: Path) -> CharacterPack:
    """`character.toml` と `voice.toml` を読む。**足りなければ例外。**"""
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
    """**欠けていたら読み込まない。** クレジットは後から足せない性質の義務である。"""
    return Credit(
        name=_text(section, "name", path),
        credit_text=_text(section, "credit_text", path),
        license_name=_text(section, "license_name", path),
        license_file=str(section.get("license_file", "")),
        license_url=str(section.get("license_url", "")),
    )
