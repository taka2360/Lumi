"""Writes out what STT actually received. **Disabled by default.**

Only when `LUMI_DEBUG_STT_DUMP=1` does each confirmed speech segment get written to
`<data_dir>/debug/stt/`, as a WAV plus a sidecar `.txt` holding what STT made of it.

## Why this exists

"Recognition is bad" splits into two very different faults, and **no log line separates
them**: either the audio handed to Whisper is already damaged (device, channel mixing,
resampling, VAD cutting the word onset), or the audio is fine and the model or the
decoding parameters are wrong. Listening to the exact buffer that went in is the only
cheap way to tell. The 48 kHz → 16 kHz aliasing found on 2026-08-17 was invisible in
every metric Core emits and obvious within one second of listening.

## Not product behavior

Same standing as `lumi.dev_probe`. **Off unless the flag is set**, and it says so at
startup when it is on — a recording that runs without the user knowing is not something
that gets to be quiet.

**Never enabled from a config file or by a provider.** An env var means the person who
turned it on was at a terminal.
"""

from __future__ import annotations

import os
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


def is_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "") == "1"


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
