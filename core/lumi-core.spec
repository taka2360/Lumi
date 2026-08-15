# -*- mode: python ; coding: utf-8 -*-
"""Core を固めて `dist/lumi-core/` に置く（Tauri が `resources` として同梱する）。

設計 → docs/decisions/ADR-021-sidecar-packaging.md / docs/architecture/setup.md

```
cd core && uv run pyinstaller lumi-core.spec --noconfirm
```

**onefile にしない。** 起動のたびに `%TEMP%` へ 21 MB 展開し、**強制終了すると展開先が残る**。
Lumi は Job Object による強制終了を「強制終了に耐える唯一の層」として使うので、
onefile では終了のたびにゴミを置いていくことになる（実測 → docs/measurements/phase0.md）。

**ネイティブ拡張は自動では入らない。** しかも入っていなくても import は通り、
実際に読み込むまで失敗しない。入ったことは `lumi-core.exe --self-check` で確かめる
（`lumi/selfcheck.py`）。

**ASIO 版の PortAudio は同梱しない。** Steinberg の ASIO SDK は非 OSS で再配布条件が別にあり、
Core = MIT の境界を汚す。`sounddevice` は環境変数 `SD_ENABLE_ASIO` があるときだけ
ASIO 版を読むので、**同梱しなければ使われることはない**（docs/licensing.md）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import get_package_paths

# ── sqlite-vec のローダブル拡張 ─────────────────────────────────
# `sqlite_vec.loadable_path()` が `dirname(__file__)/vec0` を返すので、
# パッケージと同じ相対位置に置く。**ここを間違えると Phase 2 で記憶機能が丸ごと死ぬ。**
_, sqlite_vec_dir = get_package_paths("sqlite_vec")
vec0 = Path(sqlite_vec_dir) / "vec0.dll"
if not vec0.is_file():
    raise SystemExit(f"sqlite-vec の拡張が見つからない: {vec0}")

# PortAudio 本体は `sounddevice` のフックが `_sounddevice_data` ごと集める。
# **そこに ASIO 版が混ざる**ので、Analysis の後で落とす（下の `_drop_asio`）。
binaries = [(str(vec0), "sqlite_vec")]

# 使っていないものを持ち込まない（インストーラサイズ R1）。
excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "pdb",
    "numpy",  # Phase 1 で VAD が要求したら外す
    "pytest",
    "mypy",
    "ruff",
]

a = Analysis(
    ["lumi/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=[],
    hiddenimports=["lumi.selfcheck"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

def _drop_asio(entries):
    """ASIO 版の PortAudio を配布物から落とす。

    `sounddevice` のフックは `_sounddevice_data` を丸ごと集めるため、
    **指定しなくても ASIO 版が入る**（実測。1度これで配布物に混入した）。
    """
    return [entry for entry in entries if "asio" not in Path(entry[0]).name.lower()]


a.binaries = _drop_asio(a.binaries)
a.datas = _drop_asio(a.datas)


def _require(entries, needle, message):
    """**入っているべきものが入っていることを、ビルド時に落として保証する。**

    足りないものは実行時まで分からない（import は通る）。ここで止める方が安い。
    """
    if not any(needle in entry[0].replace("\\", "/") for entry in entries):
        raise SystemExit(message)


_require(a.binaries, "sqlite_vec/vec0", "sqlite-vec の拡張が配布物に入っていない")
_require(a.binaries + a.datas, "libportaudio64bit.dll", "PortAudio が配布物に入っていない")
if any("asio" in entry[0].lower() for entry in a.binaries + a.datas):
    raise SystemExit("ASIO 版の PortAudio が残っている（再配布条件が別にある）")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lumi-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX は誤検知の温床。**サイズのために配布物を怪しくしない**
    console=True,  # stdout の構造化ログを Shell が読む。ウィンドウは Shell 側が隠す
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="lumi-core",
)
