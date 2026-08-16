"""Fetching and verification. **Never touches the network** (uses httpx's MockTransport).

Implements the test table from docs/architecture/setup.md §8.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from lumi.providers.llm import ollama
from lumi.providers.tts import aivisspeech
from lumi.setup import install as install_module
from lumi.setup.engines import (
    ALLOWED_ORIGIN_PREFIX,
    EngineArtifact,
    is_allowed_origin,
    is_allowed_redirect,
)
from lumi.setup.install import SetupError, _download, install_engine

PAYLOAD = b"lumi-test-engine-payload" * 100


def artifact_for(payload: bytes = PAYLOAD, **overrides: object) -> EngineArtifact:
    base = {
        "name": "testengine",
        "display_name": "Test Engine",
        "version": "1.0.0",
        "url": f"{ALLOWED_ORIGIN_PREFIX}1.0.0/test.7z.001",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "license_name": "LGPL-3.0",
        "license_url": "https://example.invalid/LICENSE",
        "default_port": 10101,
        "executable_name": "run.exe",
    }
    base.update(overrides)
    return EngineArtifact(**base)  # type: ignore[arg-type]


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


class TestUrlPolicy:
    def test_only_the_pinned_origin_is_allowed(self) -> None:
        assert is_allowed_origin(f"{ALLOWED_ORIGIN_PREFIX}1.2.0/x.7z.001")
        assert not is_allowed_origin("https://example.com/x.7z")
        assert not is_allowed_origin("https://github.com/other/repo/releases/download/1/x.7z")

    def test_redirects_are_restricted_to_known_hosts_over_https(self) -> None:
        assert is_allowed_redirect("https://release-assets.githubusercontent.com/a/b")
        assert is_allowed_redirect("https://objects.githubusercontent.com/a/b")
        assert not is_allowed_redirect("http://release-assets.githubusercontent.com/a/b")
        assert not is_allowed_redirect("https://evil.example.com/a/b")
        assert not is_allowed_redirect("https://githubusercontent.com.evil.example/a")


class TestDownload:
    async def test_accepts_a_matching_artifact(self, tmp_path: Path) -> None:
        artifact = artifact_for()
        destination = tmp_path / "dl.bin"

        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            await _download(
                client,
                artifact.url,
                size=artifact.size,
                sha256=artifact.sha256,
                destination=destination,
            )

        assert destination.read_bytes() == PAYLOAD

    async def test_rejects_a_hash_mismatch(self, tmp_path: Path) -> None:
        artifact = artifact_for(sha256="0" * 64)
        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            with pytest.raises(SetupError) as error:
                await _download(
                    client,
                    artifact.url,
                    size=artifact.size,
                    sha256=artifact.sha256,
                    destination=tmp_path / "dl.bin",
                )
        assert error.value.reason == "hash_mismatch"

    async def test_rejects_a_size_mismatch(self, tmp_path: Path) -> None:
        artifact = artifact_for(size=len(PAYLOAD) + 1)
        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            with pytest.raises(SetupError) as error:
                await _download(
                    client,
                    artifact.url,
                    size=artifact.size,
                    sha256=artifact.sha256,
                    destination=tmp_path / "dl.bin",
                )
        assert error.value.reason == "size_mismatch"

    async def test_stops_when_the_body_is_larger_than_pinned(self, tmp_path: Path) -> None:
        artifact = artifact_for(size=10)

        # Returns an oversized body without content-length (must be cut off mid-stream)
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=PAYLOAD, headers={"transfer-encoding": "chunked"})

        async with client_for(handler) as client:
            with pytest.raises(SetupError) as error:
                await _download(
                    client,
                    artifact.url,
                    size=artifact.size,
                    sha256=artifact.sha256,
                    destination=tmp_path / "dl.bin",
                )
        assert error.value.reason == "size_mismatch"

    async def test_follows_an_allowed_redirect(self, tmp_path: Path) -> None:
        artifact = artifact_for()
        cdn = "https://release-assets.githubusercontent.com/asset"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == artifact.url:
                return httpx.Response(302, headers={"location": cdn})
            return httpx.Response(200, content=PAYLOAD)

        async with client_for(handler) as client:
            await _download(
                client,
                artifact.url,
                size=artifact.size,
                sha256=artifact.sha256,
                destination=tmp_path / "dl.bin",
            )

    async def test_refuses_a_redirect_to_another_host(self, tmp_path: Path) -> None:
        artifact = artifact_for()

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == artifact.url:
                return httpx.Response(302, headers={"location": "https://evil.example.com/x"})
            return httpx.Response(200, content=b"malicious")

        async with client_for(handler) as client:
            with pytest.raises(SetupError) as error:
                await _download(
                    client,
                    artifact.url,
                    size=artifact.size,
                    sha256=artifact.sha256,
                    destination=tmp_path / "dl.bin",
                )
        assert error.value.reason == "redirect_not_allowed"

    async def test_refuses_an_origin_outside_the_pin(self, tmp_path: Path) -> None:
        artifact = artifact_for(url="https://evil.example.com/engine.7z")
        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            with pytest.raises(SetupError) as error:
                await _download(
                    client,
                    artifact.url,
                    size=artifact.size,
                    sha256=artifact.sha256,
                    destination=tmp_path / "dl.bin",
                )
        assert error.value.reason == "origin_not_allowed"

    async def test_reports_progress(self, tmp_path: Path) -> None:
        artifact = artifact_for()
        seen: list[float] = []

        async def progress(fraction: float) -> None:
            seen.append(fraction)

        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            await _download(
                client,
                artifact.url,
                size=artifact.size,
                sha256=artifact.sha256,
                destination=tmp_path / "dl.bin",
                progress=progress,
            )

        assert seen
        assert seen[-1] == pytest.approx(1.0)


def make_archive(path: Path, *, with_executable: bool) -> None:
    """Creates an archive that can be extracted.

    bsdtar can read both zip and 7z, so **the test uses a format it can actually
    create (zip)**. What's being verified is the "extract, find the executable, and
    commit" path — not the compression format.
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("TestEngine/README.txt", "hello")
        if with_executable:
            archive.writestr("TestEngine/run.exe", "binary")


def fake_download(
    *, archive_source: Path | None, error: SetupError | None = None
) -> Callable[..., Awaitable[None]]:
    async def _fake(
        _client: httpx.AsyncClient,
        _url: str,
        *,
        destination: Path,
        **_kwargs: object,
    ) -> None:
        if error is not None:
            raise error
        assert archive_source is not None
        # A small copy inside a test. Not worth offloading to to_thread here.
        await asyncio.to_thread(shutil.copyfile, archive_source, destination)

    return _fake


class TestInstall:
    async def test_commits_and_leaves_no_temporary_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "engine.zip"
        make_archive(source, with_executable=True)
        monkeypatch.setattr(install_module, "_download", fake_download(archive_source=source))

        engines = tmp_path / "engines"
        artifact = artifact_for()
        executable = await install_engine(artifact, engines)

        assert executable.name == "run.exe"
        assert executable.is_file()
        assert (engines / "testengine-1.0.0").is_dir()
        assert not list(engines.glob(".tmp-*")), "一時ディレクトリが残っている"

    async def test_leaves_nothing_behind_when_the_download_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            install_module,
            "_download",
            fake_download(archive_source=None, error=SetupError("network_unreachable")),
        )
        engines = tmp_path / "engines"

        with pytest.raises(SetupError):
            await install_engine(artifact_for(), engines)

        assert not (engines / "testengine-1.0.0").exists(), "確定先が作られている"
        assert not list(engines.glob(".tmp-*")), "一時ディレクトリが残っている"

    async def test_does_not_commit_when_the_executable_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "engine.zip"
        make_archive(source, with_executable=False)
        monkeypatch.setattr(install_module, "_download", fake_download(archive_source=source))
        engines = tmp_path / "engines"

        with pytest.raises(SetupError) as error:
            await install_engine(artifact_for(), engines)

        assert error.value.reason == "executable_not_found"
        assert not (engines / "testengine-1.0.0").exists()
        assert not list(engines.glob(".tmp-*"))

    async def test_is_idempotent_when_already_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "engine.zip"
        make_archive(source, with_executable=True)
        monkeypatch.setattr(install_module, "_download", fake_download(archive_source=source))
        engines = tmp_path / "engines"

        first = await install_engine(artifact_for(), engines)

        # The second time never fetches (rigged so calling _download raises)
        monkeypatch.setattr(
            install_module,
            "_download",
            fake_download(archive_source=None, error=SetupError("should_not_download")),
        )
        second = await install_engine(artifact_for(), engines)
        assert first == second

    async def test_fails_explicitly_without_tar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**No alternative is sought.** Fails if extraction isn't possible."""
        source = tmp_path / "engine.zip"
        make_archive(source, with_executable=True)
        monkeypatch.setattr(install_module, "_download", fake_download(archive_source=source))
        monkeypatch.delenv("SystemRoot", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _: None)

        with pytest.raises(SetupError) as error:
            await install_engine(artifact_for(), tmp_path / "engines")
        assert error.value.reason == "tar_not_found"


class TestNetworkOptional:
    """**No external communication until the user chooses to**
    (docs/architecture/setup.md Principle 1).

    A static check. Guarantees "no communication unless called" in code form
    (.claude/rules/tests.md "static checks are tests too").
    """

    #: Modules allowed to hold an HTTP client, and why.
    #: **When adding one, always write down "when and where it communicates."**
    ALLOWED_HTTP: ClassVar[dict[str, str]] = {
        "setup/install.py": "エンジンの取得。ユーザーが選んだときだけ呼ばれる",
        "providers/tts/aivisspeech.py": "外部エンジン。127.0.0.1 のみ（下のテストで固定）",
        "providers/llm/ollama.py": "外部エンジン。127.0.0.1 のみ（下のテストで固定）",
    }

    def test_http_is_confined_to_known_modules(self) -> None:
        root = Path(__file__).resolve().parent.parent / "lumi"
        offenders = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            if "httpx" in path.read_text(encoding="utf-8")
        )
        assert offenders == sorted(self.ALLOWED_HTTP), (
            "HTTP クライアントを持ってよいのは取得処理と外部エンジンのクライアントだけ。"
            "他から使うと、ユーザーの選択より前に外部通信が起きうる"
        )

    def test_the_engine_client_only_talks_to_localhost(self) -> None:
        """The engine's destination is **never made configurable**.

        Making it configurable would turn this into "a feature where Lumi sends
        text to be read aloud to an arbitrary server."
        """
        module = Path(aivisspeech.__file__)
        urls = re.findall(r"https?://[^\"']*", module.read_text(encoding="utf-8"))
        assert urls, "URL の組み立て方が変わった。このテストを見直すこと"
        assert all(url.startswith("http://{HOST}:") for url in urls), urls
        assert aivisspeech.HOST == "127.0.0.1"

    def test_the_llm_client_only_talks_to_localhost(self) -> None:
        """**Conversation content never leaves the machine.** Remote inference is added as a
        separate Provider (ADR-023).
        """
        module = Path(ollama.__file__)
        urls = re.findall(r"https?://[^\"']*", module.read_text(encoding="utf-8"))
        assert urls, "URL の組み立て方が変わった。このテストを見直すこと"
        assert all(
            url.startswith(("http://{HOST}:", "https://github.com", "https://ollama.com"))
            for url in urls
        ), urls
        assert ollama.HOST == "127.0.0.1"

    def test_the_installer_is_only_reachable_through_the_coordinator(self) -> None:
        root = Path(__file__).resolve().parent.parent / "lumi"
        callers = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            if "install_engine(" in path.read_text(encoding="utf-8") and path.name != "install.py"
        ]
        assert callers == ["setup/coordinator.py"]
