"""Where user data and bundled assets live.

**Confined to one place.** Scattering them makes "delete everything" impossible to honor
(docs/roadmap.md Phase 2 🔴 privacy item #5).

Phase 0 only uses `engines_dir` and `setup_state_file`. Where the memory DB / audit log live is
decided in Phase 2 (after `contracts/privacy.md` is written).

**User data (erasable) and bundled assets (read-only) are different things.** The former
lives under `data_dir()`, the latter under `content_dir()`, and what gets erased differs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Overrides where bundled assets live, for dev/tests
CONTENT_DIR_ENV = "LUMI_CONTENT_DIR"


def data_dir() -> Path:
    """Root of Lumi's user data."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Lumi"
    # Non-Windows falls back to XDG (out of scope for Phase 0, but keep the path decision unified).
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "lumi"


def content_dir() -> Path:
    """Where Content Packs live. **A bundled asset, not user data.**

    In the packed executable, PyInstaller's extraction dir; in dev, the repo root's `content/`.
    **Existence is not checked here** — noticing it's missing is the loader's job, and
    swallowing that here would make "why is there no persona" impossible to diagnose.
    """
    override = os.environ.get(CONTENT_DIR_ENV)
    if override:
        return Path(override)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "content"
    return Path(__file__).resolve().parents[2] / "content"


def default_character_dir() -> Path:
    """The default character (Content Pack) → docs/architecture/extension.md §9"""
    return content_dir() / "characters" / "lumi"


def engines_dir() -> Path:
    """Where external engines get installed → docs/architecture/setup.md §5"""
    return data_dir() / "engines"


def models_dir() -> Path:
    """Where models fetched at runtime (STT, etc.) live → ADR-023

    **Not included in the distributable.** Hundreds of MB, which would directly hit R1
    (installer size), so it's fetched only after consent.
    """
    return data_dir() / "models"


def stt_models_dir() -> Path:
    """Where speech-recognition models live.

    **One definition, used by both the fetcher and the Provider.** They had drifted by a
    single path segment, and the only symptom was the setup panel offering to fetch a
    model that was already on disk (observed 2026-08-17) — nothing failed, it just
    quietly asked again.

    Kept under its own subdirectory because **models are deleted per kind**: replacing
    the speech model has nothing to do with an embedding model (Phase 2).
    """
    return models_dir() / "whisper"


def setup_state_file() -> Path:
    """File that remembers whether first-run setup has been "answered."

    The settings storage format itself is undecided (roadmap open item #9 / Phase 1).
    **This holds only the single fact "already asked."** Not a general-purpose settings store.
    """
    return data_dir() / "setup.json"
