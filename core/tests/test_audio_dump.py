"""The STT debug dump. **Off unless the env flag is set.**

docs/architecture/audio.md §8 "音声入力のデバッグ"
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from lumi.audio import dump as dump_module
from lumi.audio.dump import ENV_FLAG, MAX_SEGMENTS, SttDump, open_dump
from lumi.audio.wav import decode_wav, encode_wav


def test_encode_round_trips_through_the_decoder() -> None:
    mono = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32)
    wav = decode_wav(encode_wav(mono, 16000))
    assert wav.sample_rate == 16000
    assert wav.channels == 1
    assert wav.sample_width == 2
    assert np.allclose(np.frombuffer(wav.frames, dtype="<i2") / 32767.0, mono, atol=1e-4)


def test_encode_clips_rather_than_normalising() -> None:
    """**A scaled dump would hide clipping**, which is one thing the dump exists to reveal."""
    frames = decode_wav(encode_wav(np.array([4.0, -4.0], dtype=np.float32), 16000)).frames
    assert list(np.frombuffer(frames, dtype="<i2")) == [32767, -32767]


def test_a_source_checkout_dumps_and_the_distributable_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The person running from source is the person debugging it.**

    A shipped Lumi is a different situation: nothing gets recorded there unasked.
    """
    monkeypatch.delenv(ENV_FLAG, raising=False)
    monkeypatch.setattr(dump_module, "is_frozen", lambda: False)
    assert open_dump(tmp_path) is not None
    monkeypatch.setattr(dump_module, "is_frozen", lambda: True)
    assert open_dump(tmp_path) is None


def test_the_env_flag_overrides_in_both_directions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dump_module, "is_frozen", lambda: True)
    monkeypatch.setenv(ENV_FLAG, "1")
    assert open_dump(tmp_path) is not None
    monkeypatch.setattr(dump_module, "is_frozen", lambda: False)
    monkeypatch.setenv(ENV_FLAG, "0")
    assert open_dump(tmp_path) is None


def test_a_segment_lands_next_to_what_stt_made_of_it(tmp_path: Path) -> None:
    """The WAV and the transcription **are the evidence only as a pair.**"""
    dump = SttDump(tmp_path / "stt")
    path = dump.write(np.zeros(160, dtype=np.float32), 16000)
    assert path is not None
    dump.annotate(path, "おはよう")

    assert decode_wav(path.read_bytes()).sample_rate == 16000
    assert path.with_suffix(".txt").read_text(encoding="utf-8") == "おはよう"


def test_writes_stop_at_the_cap(tmp_path: Path) -> None:
    """**A dump left on overnight must not fill the disk.**"""
    dump = SttDump(tmp_path)
    silence = np.zeros(16, dtype=np.float32)
    written = [dump.write(silence, 16000) for _ in range(MAX_SEGMENTS + 5)]
    assert sum(1 for path in written if path is not None) == MAX_SEGMENTS
    assert written[-1] is None


def test_a_broken_dump_never_takes_down_a_turn(tmp_path: Path) -> None:
    """The directory is a file. **Swallowed** — a debug aid must not cost an utterance."""
    blocker = tmp_path / "stt"
    blocker.write_bytes(b"")
    dump = SttDump(blocker)
    assert dump.write(np.zeros(16, dtype=np.float32), 16000) is None
