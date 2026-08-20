"""Writes out what STT actually received. **On in dev, off in the distributable.**

Each confirmed speech segment is written to `<data_dir>/debug/stt/` as a **16 kHz mono
WAV** (playable in anything) plus a sidecar `.txt` holding what STT made of it.

| Where | Default | Override |
|---|---|---|
| Running from source (`uv run lumi-core`, `pnpm dev`) | **on** | `LUMI_DEBUG_STT_DUMP=0` |
| The frozen distributable | **off** | `LUMI_DEBUG_STT_DUMP=1` |

**Whoever runs Core from source is the person developing it**, and the one diagnostic
that matters most for voice input should not need a flag they have to remember. A shipped
Lumi is a different situation: nothing records there unless someone asks for it.

## Why this exists

"Recognition is bad" splits into two very different faults, and **no log line separates
them**: either the audio handed to Whisper is already damaged (device, channel mixing,
resampling, VAD cutting the word onset), or the audio is fine and the model or the
decoding parameters are wrong. Listening to the exact buffer that went in is the only
cheap way to tell. The 48 kHz → 16 kHz aliasing found on 2026-08-17 was invisible in
every metric Core emits and obvious within one second of listening.

## Not product behavior

Same standing as `lumi.dev_probe`. **It says so at startup whenever it is on**, and names
the directory — a recording that runs without the user knowing is not something that gets
to be quiet.

**Never enabled from a config file or by a provider.** Source-vs-frozen and an env var are
both things only the person at the terminal controls.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Final

from lumi import logging as lumi_logging
from lumi.audio.ring import Samples
from lumi.audio.wav import encode_wav

log = lumi_logging.get_logger(__name__)

ENV_FLAG: Final = "LUMI_DEBUG_STT_DUMP"

#: Cap on how many segments one session writes. **A dump left on overnight must not fill
#: the disk.** Hitting the cap is logged once, not silently.
MAX_SEGMENTS: Final = 200


def is_frozen() -> bool:
    """Whether this is the PyInstaller distributable rather than a source checkout.

    `_MEIPASS` is the extraction directory, and **only exists in the frozen build**
    (same signal `paths.content_dir` uses → ADR-021).
    """
    return getattr(sys, "_MEIPASS", None) is not None


def is_enabled() -> bool:
    """**Default on from source, off when frozen.** `LUMI_DEBUG_STT_DUMP` overrides either way."""
    override = os.environ.get(ENV_FLAG, "")
    if override:
        return override != "0"
    return not is_frozen()


class SttDump:
    """Per-session sink. **Every failure here is swallowed** — a broken debug aid must never
    take down a turn.
    """

    __slots__ = ("_count", "_directory", "_exhausted")

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._count = 0
        self._exhausted = False

    @property
    def directory(self) -> Path:
        return self._directory

    def write(self, audio: Samples, sample_rate: int) -> Path | None:
        """Write one segment. Returns the path, or `None` if nothing was written."""
        if self._exhausted:
            return None
        if self._count >= MAX_SEGMENTS:
            self._exhausted = True
            log.warning("stt_dump.limit_reached", limit=MAX_SEGMENTS, dir=str(self._directory))
            return None
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            path = self._directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{self._count:03d}.wav"
            path.write_bytes(encode_wav(audio, sample_rate))
        except OSError as error:
            log.warning("stt_dump.write_failed", error=str(error))
            return None
        self._count += 1
        return path

    def annotate(self, path: Path, text: str) -> None:
        """Record what STT made of that WAV, next to it. **Makes the pair the evidence.**"""
        try:
            path.with_suffix(".txt").write_text(text, encoding="utf-8")
        except OSError as error:
            log.warning("stt_dump.annotate_failed", error=str(error))


def open_dump(directory: Path) -> SttDump | None:
    """`None` unless the env flag is set. **The only way one gets created.**"""
    if not is_enabled():
        return None
    log.warning("stt_dump.enabled", dir=str(directory), env=ENV_FLAG)
    return SttDump(directory)
