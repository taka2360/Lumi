"""Content Pack. **docs/architecture/extension.md tests 13 / 13b.**

Both are fail-closed. Never builds a "couldn't read it, so falling back to a default" path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lumi import paths
from lumi.content.pack import ContentPackError, load_character

CHARACTER = """
[character]
name = "Lumi"
persona = "あなたは Lumi。"
"""

VOICE = """
[voice]

[credit]
name = "既定の音声合成モデル"
credit_text = "音声: AivisSpeech"
license_name = "ACML 1.0"
"""


def pack(tmp_path: Path, *, character: str = CHARACTER, voice: str = VOICE) -> Path:
    root = tmp_path / "lumi"
    root.mkdir()
    (root / "character.toml").write_text(character, encoding="utf-8")
    (root / "voice.toml").write_text(voice, encoding="utf-8")
    return root


def test_a_minimal_pack_loads(tmp_path: Path) -> None:
    loaded = load_character(pack(tmp_path))

    assert loaded.name == "Lumi"
    assert loaded.persona == "あなたは Lumi。"
    assert loaded.voice.speaker is None
    assert loaded.voice.credit.credit_text == "音声: AivisSpeech"


def test_a_speaker_id_is_read(tmp_path: Path) -> None:
    root = pack(tmp_path, voice=VOICE.replace("[voice]", "[voice]\nspeaker = 42"))
    assert load_character(root).voice.speaker == 42


# ── fail-closed ─────────────────────────────────────────────


def test_a_pack_with_code_is_rejected(tmp_path: Path) -> None:
    """**Test 13.** Content Packs are easily shared and redistributed.

    Including code would make a Content Pack the same threat as an Extension.
    """
    root = pack(tmp_path)
    (root / "hook.py").write_text("print('hi')", encoding="utf-8")

    with pytest.raises(ContentPackError, match="コード"):
        load_character(root)


def test_code_nested_deeper_is_also_rejected(tmp_path: Path) -> None:
    root = pack(tmp_path)
    (root / "motions").mkdir()
    (root / "motions" / "evil.dll").write_bytes(b"\x00")

    with pytest.raises(ContentPackError, match="コード"):
        load_character(root)


def test_a_pack_without_credit_is_rejected(tmp_path: Path) -> None:
    """**Test 13b.** Credit attribution is an obligation from the voice source's terms, and can't be
    added later.
    """
    root = pack(tmp_path, voice="[voice]\nspeaker = 0\n")

    with pytest.raises(ContentPackError, match=r"\[credit\]"):
        load_character(root)


def test_an_incomplete_credit_is_rejected(tmp_path: Path) -> None:
    root = pack(tmp_path, voice='[voice]\n\n[credit]\nname = "だれか"\n')

    with pytest.raises(ContentPackError, match="credit_text"):
        load_character(root)


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    root = pack(tmp_path)
    (root / "voice.toml").unlink()

    with pytest.raises(ContentPackError, match=r"voice\.toml"):
        load_character(root)


def test_a_missing_directory_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ContentPackError, match="無い"):
        load_character(tmp_path / "nope")


def test_broken_toml_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ContentPackError, match="読めない"):
        load_character(pack(tmp_path, character="[character\n"))


def test_a_missing_persona_is_reported(tmp_path: Path) -> None:
    """**Lumi without a persona isn't Lumi.** Never substituted with an empty string."""
    with pytest.raises(ContentPackError, match="persona"):
        load_character(pack(tmp_path, character='[character]\nname = "Lumi"\n'))


# ── The bundled default pack ────────────────────────────────


def test_the_bundled_default_pack_loads() -> None:
    """**Confirms in CI that what ships in the distributable can actually be read.**

    A failure here means either the `content/` toml got broken, or code was
    accidentally placed inside it.
    """
    loaded = load_character(paths.default_character_dir())
    assert loaded.persona
    assert loaded.voice.credit.credit_text
