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


MODEL = """
[model]
file = "model.vrm"
format = "vrm0"

[model.credit]
name = "光莉 / ひかり"
credit_text = "3Dモデル: 光莉 / ひかり（あわ）"
license_name = "VRoid Hub 利用条件（作者設定）"
"""


def pack(
    tmp_path: Path,
    *,
    character: str = CHARACTER,
    voice: str = VOICE,
    model_file: bool = False,
) -> Path:
    root = tmp_path / "lumi"
    root.mkdir(parents=True)
    (root / "character.toml").write_text(character, encoding="utf-8")
    (root / "voice.toml").write_text(voice, encoding="utf-8")
    if model_file:
        (root / "model.vrm").write_bytes(b"glTF")
    return root


def test_a_minimal_pack_loads(tmp_path: Path) -> None:
    loaded = load_character(pack(tmp_path))

    assert loaded.name == "Lumi"
    assert loaded.persona == "あなたは Lumi。"
    assert loaded.voice.speaker is None
    assert loaded.voice.credit.credit_text == "音声: AivisSpeech"
    assert loaded.voice.volume == 0.4


def test_a_speaker_id_is_read(tmp_path: Path) -> None:
    root = pack(tmp_path, voice=VOICE.replace("[voice]", "[voice]\nspeaker = 42"))
    assert load_character(root).voice.speaker == 42


def test_a_volume_is_read(tmp_path: Path) -> None:
    root = pack(tmp_path, voice=VOICE.replace("[voice]", "[voice]\nvolume = 0.8"))
    assert load_character(root).voice.volume == 0.8


def test_an_integer_volume_is_read_as_float(tmp_path: Path) -> None:
    root = pack(tmp_path, voice=VOICE.replace("[voice]", "[voice]\nvolume = 1"))
    assert load_character(root).voice.volume == 1.0


# ── 見た目（[model]） ────────────────────────────────────────


def test_a_pack_without_a_model_is_valid(tmp_path: Path) -> None:
    """**声だけの Content Pack も Content Pack である。** プレースホルダで動く。"""
    assert load_character(pack(tmp_path)).model is None


def test_a_declared_model_is_read(tmp_path: Path) -> None:
    loaded = load_character(pack(tmp_path, character=CHARACTER + MODEL, model_file=True))

    assert loaded.model is not None
    assert loaded.model.path.name == "model.vrm"
    assert loaded.model.format == "vrm0"
    assert loaded.model.credit.credit_text == "3Dモデル: 光莉 / ひかり（あわ）"


def test_a_model_that_is_not_shipped_is_rejected(tmp_path: Path) -> None:
    """**黙ってプレースホルダに落とさない。** 名前を書いたのに実体が無いのは壊れている。"""
    with pytest.raises(ContentPackError, match=r"model\.vrm"):
        load_character(pack(tmp_path, character=CHARACTER + MODEL))


def test_a_model_without_credit_is_rejected(tmp_path: Path) -> None:
    """★ **アセットを同梱するなら、クレジットを宣言する** (extension.md §9).

    `voice.toml` の `[credit]` と同じ規則。**「後で足す」ができない性質のもの**であり、
    その license がクレジットを要求するかどうかとは別の判断（Lumi はどちらでも出す）。
    """
    declared = CHARACTER + '\n[model]\nfile = "model.vrm"\n'
    with pytest.raises(ContentPackError, match=r"\[credit\]"):
        load_character(pack(tmp_path, character=declared, model_file=True))


def test_a_model_outside_the_pack_is_rejected(tmp_path: Path) -> None:
    """★ **Content Pack は再配布されるデータであり、境界の外を指してよい根拠が無い。**

    `root / declared` は絶対パスをそのまま採用し、`..` も素通りする。読めた path は
    そのまま Stage に配信される（ADR-029）ので、**パックが任意のローカルファイルを
    名指しできてしまう。** Shell の asset scope が配信を拒むとしても、
    向こう側も見ているから成立する境界は境界ではない。
    """
    outside = tmp_path / "secret.vrm"
    outside.write_bytes(b"glTF")

    for index, declared in enumerate(("../secret.vrm", outside.as_posix())):
        character = CHARACTER + MODEL.replace('file = "model.vrm"', f'file = "{declared}"')
        root = pack(tmp_path / str(index), character=character, model_file=True)
        with pytest.raises(ContentPackError, match=r"Content Pack の外"):
            load_character(root)


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


def test_an_invalid_volume_type_is_rejected(tmp_path: Path) -> None:
    root = pack(tmp_path, voice=VOICE.replace("[voice]", '[voice]\nvolume = "loud"'))
    with pytest.raises(ContentPackError, match="volume"):
        load_character(root)


def test_a_negative_volume_is_rejected(tmp_path: Path) -> None:
    root = pack(tmp_path, voice=VOICE.replace("[voice]", "[voice]\nvolume = -0.1"))
    with pytest.raises(ContentPackError, match="volume"):
        load_character(root)


def test_a_too_large_volume_is_rejected(tmp_path: Path) -> None:
    root = pack(tmp_path, voice=VOICE.replace("[voice]", "[voice]\nvolume = 2.1"))
    with pytest.raises(ContentPackError, match="volume"):
        load_character(root)


# ── The bundled default pack ────────────────────────────────


def test_the_bundled_default_pack_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Confirms in CI that the tracked part of the default pack can be read.**

    The real VRM is deliberately absent from git (docs/licensing.md §4.5). Its presence in
    the distributable is checked by ``lumi-core.spec``; this test checks the tracked TOML and
    still scans the real pack directory for accidentally committed code.
    """
    root = paths.default_character_dir()
    model_path = root / "model.vrm"
    path_is_file = Path.is_file

    def is_file_with_bundled_model_stub(path: Path) -> bool:
        return path == model_path or path_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file_with_bundled_model_stub)
    loaded = load_character(root)
    assert loaded.persona
    assert loaded.voice.credit.credit_text
    # **The value itself is a tuning knob**, not a contract. Pinning it here made an
    # ordinary volume adjustment fail CI (2026-08-17); what matters is that it parsed
    # and landed in a usable range.
    assert 0.0 < loaded.voice.volume <= 2.0
    # ★ **既定キャラクターには見た目がある** — docs/licensing.md §4.5（光莉 / ひかり）。
    # `[model]` はトップレベルの表なので、`[character]` の中を探すと黙って `None` になる
    # （実際にそう書いて、この行が無ければ気づかなかった。2026-08-19）
    assert loaded.model is not None, "既定 Content Pack がモデルを宣言していない"
    assert loaded.model.path == model_path
    assert loaded.model.credit.credit_text
