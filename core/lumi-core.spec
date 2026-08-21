# -*- mode: python ; coding: utf-8 -*-
"""Builds Core and places it in `dist/lumi-core/` (bundled by Tauri as `resources`).

Design: docs/decisions/ADR-021-sidecar-packaging.md / docs/architecture/setup.md

```
cd core && uv run pyinstaller lumi-core.spec --noconfirm --clean
```

The repository build also passes `--clean`. PyInstaller's cached EXE target
tracks the icon path, but not reliably the contents of the icon file, so a
changed `icon.ico` can otherwise leave the previous icon in `lumi-core.exe`.

**Not built as onefile.** It would extract 21 MB to `%TEMP%` on every launch,
and **a force-kill leaves the extracted copy behind.** Lumi uses a Job Object
force-kill as "the only layer that survives a forced termination," so onefile
would leave garbage behind on every exit (see docs/measurements/phase0.md).

**Native extensions aren't included automatically.** Worse, import still
succeeds even without them, and it only fails once actually loaded. Whether
they're present is checked via `lumi-core.exe --self-check` (`lumi/selfcheck.py`).

**The ASIO build of PortAudio is not bundled.** Steinberg's ASIO SDK is
non-OSS with separate redistribution terms, which would taint the Core = MIT
boundary. `sounddevice` only loads the ASIO build when the `SD_ENABLE_ASIO`
environment variable is set, so **not bundling it means it's never used** (docs/licensing.md).
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import get_package_paths

# -- sqlite-vec's loadable extension ---------------------------------
# `sqlite_vec.loadable_path()` returns `dirname(__file__)/vec0`, so it's
# placed at the same relative position as the package. **Getting this wrong kills memory entirely in Phase 2.**
_, sqlite_vec_dir = get_package_paths("sqlite_vec")
vec0 = Path(sqlite_vec_dir) / "vec0.dll"
if not vec0.is_file():
    raise SystemExit(f"sqlite-vec extension not found: {vec0}")

# -- Silero VAD's ONNX file -----------------------------------------
# **Borrows the copy faster-whisper already bundles** (docs/licensing.md, section 4.6 / ADR-023).
# PyPI's `silero-vad` isn't used since it depends on torch (a direct hit to R1).
# **This is a dependency on the package's internal structure.** This breaks if an update moves the path.
_, faster_whisper_dir = get_package_paths("faster_whisper")
silero = Path(faster_whisper_dir) / "assets" / "silero_vad_v6.onnx"
if not silero.is_file():
    raise SystemExit(f"Silero VAD ONNX file not found: {silero}")

# -- The default Content Pack ---------------------------------------
# **A Lumi without a personality isn't Lumi.** `paths.content_dir()` looks
# at `sys._MEIPASS/content` in the built executable.
content = Path(SPECPATH).parent / "content"
if not (content / "characters" / "lumi" / "character.toml").is_file():
    raise SystemExit(f"Default Content Pack not found: {content}")

# -- The application icon -------------------------------------------
# Keep the bundled Core executable visually consistent with the Shell,
# installer, and shortcuts. The Windows distributable uses the ICO asset.
app_icon = Path(SPECPATH).parent / "shell" / "src-tauri" / "icons" / "icon.ico"
if not app_icon.is_file():
    raise SystemExit(f"Application icon not found: {app_icon}")

# PortAudio itself is gathered by `sounddevice`'s hook, along with all of `_sounddevice_data`.
# **The ASIO build ends up mixed in there**, so it's dropped after Analysis (`_drop_asio` below).
binaries = [(str(vec0), "sqlite_vec")]

datas = [
    (str(silero), "faster_whisper/assets"),
    (str(content), "content"),
]

# Doesn't drag in what isn't used (installer size R1).
# **numpy can't be excluded.** Required by Phase 1's VAD, resampling, and playback (Step D)
excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "pdb",
    "pytest",
    "mypy",
    "ruff",
]

a = Analysis(
    ["lumi/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["lumi.selfcheck"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

def _drop_asio(entries):
    """Drops the ASIO build of PortAudio from the distributable.

    `sounddevice`'s hook gathers all of `_sounddevice_data`, so
    **the ASIO build gets included even without requesting it** (observed once ending up in the distributable this way).
    """
    return [entry for entry in entries if "asio" not in Path(entry[0]).name.lower()]


a.binaries = _drop_asio(a.binaries)
a.datas = _drop_asio(a.datas)


def _pin_vcruntime(entries):
    """**Pins the VC++ runtime to the one in System32.**

    PyInstaller searches PATH for the required DLLs. On this machine,
    `C:\\Program Files\\Microsoft\\jdk-11.0.16.101-hotspot\\bin\\msvcp140.dll`
    (**14.16 / VS2017**) was found first and ended up in the distributable.
    onnxruntime.dll requires a newer version, so **DLL initialization fails
    only in the built executable** (observed 2026-08-16; doesn't happen in the dev environment).

    The symptom is "Silero VAD can't load" = **barge-in dies completely.**
    And since import still succeeds up to that point, it isn't noticed until
    someone actually talks to Lumi.

    The real problem is that the distributable's behavior depends on the
    build machine's PATH, so **instead of fixing PATH, the source is pinned to System32 here.**
    """
    system32 = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32"
    pinned = []
    for dest, source, kind in entries:
        name = Path(dest).name
        if name.lower().startswith(("msvcp140", "vcruntime140")) and Path(dest).parent == Path("."):
            replacement = system32 / name
            if not replacement.is_file():
                raise SystemExit(
                    f"System32 does not contain {name}; install the Microsoft Visual C++ Redistributable"
                )
            pinned.append((dest, str(replacement), kind))
        else:
            pinned.append((dest, source, kind))
    return pinned


a.binaries = _pin_vcruntime(a.binaries)


def _require(entries, needle, message):
    """**Guarantees at build time, by failing the build, that what should be included actually is.**

    A missing piece otherwise wouldn't be noticed until runtime (import
    still succeeds). Stopping here is cheaper.
    """
    if not any(needle in entry[0].replace("\\", "/") for entry in entries):
        raise SystemExit(message)


_require(a.binaries, "sqlite_vec/vec0", "sqlite-vec extension is missing from the distributable")
_require(a.binaries + a.datas, "libportaudio64bit.dll", "PortAudio is missing from the distributable")
_require(a.datas, "silero_vad_v6.onnx", "Silero VAD is missing from the distributable (barge-in will not work)")
_require(a.datas, "content/characters/lumi/character.toml", "Default Content Pack is missing from the distributable")
# **The default character model.** It is redistributable and must be included (docs/licensing.md, section 4.5).
# If it is missing, Lumi still starts with a placeholder, so **fail here**.
_require(a.datas, "content/characters/lumi/model.vrm", "Default VRM is missing from the distributable (Lumi would ship with a placeholder)")
_require(a.binaries, "onnxruntime", "ONNX Runtime is missing from the distributable")
if any("asio" in entry[0].lower() for entry in a.binaries + a.datas):
    raise SystemExit("The ASIO build of PortAudio remains in the distributable (it has separate redistribution terms)")

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
    upx=False,  # UPX is a common source of false-positive AV flags. **Never make the distributable look suspicious just to save size**
    console=True,  # Shell reads the structured stdout log. Shell hides the window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=str(app_icon),
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
