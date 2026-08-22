"""The storage self-checks.

`lumi-core.exe --self-check` is what says whether a **built** distributable works, so
the checks themselves are the thing nobody exercises until a build exists. The three
here are pure-CPU and run anywhere, and their failure mode is the expensive one: a
distributable that starts, looks fine, and cannot open its own memory.

They are the checks that ran green in the dev environment and failed in the sidecar
because a refused `sqlite3` connection was left open (2026-08-22).
"""

from __future__ import annotations

import sys

import pytest

from lumi.selfcheck import (
    check_encrypted_sqlite,
    check_fts5,
    check_secret_store,
    check_sqlite_vec,
)


def test_encrypted_sqlite() -> None:
    result = check_encrypted_sqlite()
    assert result.ok, result.detail


def test_sqlite_vec() -> None:
    result = check_sqlite_vec()
    assert result.ok, result.detail


def test_fts5() -> None:
    result = check_fts5()
    assert result.ok, result.detail


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is the Windows implementation")
def test_secret_store() -> None:
    result = check_secret_store()
    assert result.ok, result.detail
