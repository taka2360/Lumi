"""既存インストールの検出。**外部通信しない**ことを含めて確かめる。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lumi import paths as paths_module
from lumi.setup import detect as detect_module
from lumi.setup.detect import (
    KNOWN_ENGINES,
    candidate_executables,
    detect_engines,
    find_installed_by_lumi,
    is_port_open,
)
from lumi.setup.engines import AIVISSPEECH_ENGINE


def engine(name: str) -> detect_module.KnownEngine:
    return next(item for item in KNOWN_ENGINES if item.name == name)


class TestCandidatePaths:
    def test_builds_paths_from_the_environment(self) -> None:
        paths = candidate_executables(
            engine("aivisspeech"),
            {"LOCALAPPDATA": r"C:\u\AppData\Local", "ProgramFiles": r"C:\Program Files"},
        )
        assert paths == [
            Path(r"C:\u\AppData\Local\Programs\AivisSpeech\AivisSpeech-Engine\run.exe"),
            Path(r"C:\Program Files\AivisSpeech\AivisSpeech-Engine\run.exe"),
        ]

    def test_skips_missing_environment_variables(self) -> None:
        assert candidate_executables(engine("voicevox"), {}) == []


class TestFindInstalledByLumi:
    def test_finds_the_executable_anywhere_in_the_tree(self, tmp_path: Path) -> None:
        nested = tmp_path / f"{AIVISSPEECH_ENGINE.name}-{AIVISSPEECH_ENGINE.version}" / "deep"
        nested.mkdir(parents=True)
        (nested / AIVISSPEECH_ENGINE.executable_name).write_text("x")

        found = find_installed_by_lumi(AIVISSPEECH_ENGINE, tmp_path)
        assert found is not None
        assert found.name == AIVISSPEECH_ENGINE.executable_name

    def test_returns_none_when_not_installed(self, tmp_path: Path) -> None:
        assert find_installed_by_lumi(AIVISSPEECH_ENGINE, tmp_path) is None


class TestPortProbe:
    async def test_reports_an_open_local_port(self) -> None:
        async def handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            assert await is_port_open(port)
        finally:
            server.close()
            await server.wait_closed()

    async def test_reports_a_closed_port(self) -> None:
        # 使われていないであろう高位ポート。開いていなければ False。
        assert not await is_port_open(59_999)


class TestDetectEngines:
    async def test_finds_nothing_on_a_clean_machine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(paths_module, "engines_dir", lambda: tmp_path / "engines")

        async def closed(_port: int, *, host: str = "127.0.0.1") -> bool:
            del host
            return False

        monkeypatch.setattr(detect_module, "is_port_open", closed)
        assert await detect_engines({}) == []

    async def test_finds_an_installed_engine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        executable = tmp_path / "Programs" / "AivisSpeech" / "AivisSpeech-Engine" / "run.exe"
        executable.parent.mkdir(parents=True)
        executable.write_text("x")
        monkeypatch.setattr(paths_module, "engines_dir", lambda: tmp_path / "engines")

        async def closed(_port: int, *, host: str = "127.0.0.1") -> bool:
            del host
            return False

        monkeypatch.setattr(detect_module, "is_port_open", closed)

        found = await detect_engines({"LOCALAPPDATA": str(tmp_path)})
        assert [item.name for item in found] == ["aivisspeech"]
        assert found[0].executable == executable
        assert not found[0].running
