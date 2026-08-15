"""取得と検証。**ネットワークに出ない**（httpx の MockTransport を使う）。

docs/architecture/setup.md §8 のテスト表を実装する。
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
            await _download(client, artifact, destination, None)

        assert destination.read_bytes() == PAYLOAD

    async def test_rejects_a_hash_mismatch(self, tmp_path: Path) -> None:
        artifact = artifact_for(sha256="0" * 64)
        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            with pytest.raises(SetupError) as error:
                await _download(client, artifact, tmp_path / "dl.bin", None)
        assert error.value.reason == "hash_mismatch"

    async def test_rejects_a_size_mismatch(self, tmp_path: Path) -> None:
        artifact = artifact_for(size=len(PAYLOAD) + 1)
        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            with pytest.raises(SetupError) as error:
                await _download(client, artifact, tmp_path / "dl.bin", None)
        assert error.value.reason == "size_mismatch"

    async def test_stops_when_the_body_is_larger_than_pinned(self, tmp_path: Path) -> None:
        artifact = artifact_for(size=10)

        # content-length を付けずに大きい本文を返す（途中で打ち切れること）
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=PAYLOAD, headers={"transfer-encoding": "chunked"})

        async with client_for(handler) as client:
            with pytest.raises(SetupError) as error:
                await _download(client, artifact, tmp_path / "dl.bin", None)
        assert error.value.reason == "size_mismatch"

    async def test_follows_an_allowed_redirect(self, tmp_path: Path) -> None:
        artifact = artifact_for()
        cdn = "https://release-assets.githubusercontent.com/asset"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == artifact.url:
                return httpx.Response(302, headers={"location": cdn})
            return httpx.Response(200, content=PAYLOAD)

        async with client_for(handler) as client:
            await _download(client, artifact, tmp_path / "dl.bin", None)

    async def test_refuses_a_redirect_to_another_host(self, tmp_path: Path) -> None:
        artifact = artifact_for()

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == artifact.url:
                return httpx.Response(302, headers={"location": "https://evil.example.com/x"})
            return httpx.Response(200, content=b"malicious")

        async with client_for(handler) as client:
            with pytest.raises(SetupError) as error:
                await _download(client, artifact, tmp_path / "dl.bin", None)
        assert error.value.reason == "redirect_not_allowed"

    async def test_refuses_an_origin_outside_the_pin(self, tmp_path: Path) -> None:
        artifact = artifact_for(url="https://evil.example.com/engine.7z")
        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            with pytest.raises(SetupError) as error:
                await _download(client, artifact, tmp_path / "dl.bin", None)
        assert error.value.reason == "origin_not_allowed"

    async def test_reports_progress(self, tmp_path: Path) -> None:
        artifact = artifact_for()
        seen: list[float] = []

        async def progress(fraction: float) -> None:
            seen.append(fraction)

        async with client_for(lambda _: httpx.Response(200, content=PAYLOAD)) as client:
            await _download(client, artifact, tmp_path / "dl.bin", progress)

        assert seen
        assert seen[-1] == pytest.approx(1.0)


def make_archive(path: Path, *, with_executable: bool) -> None:
    """展開できる書庫を作る。

    bsdtar は zip も 7z も読めるので、**テストでは作れる形式（zip）を使う**。
    確かめたいのは「展開して実行体を見つけて確定する」経路であって、圧縮形式ではない。
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
        _artifact: EngineArtifact,
        destination: Path,
        _progress: object,
    ) -> None:
        if error is not None:
            raise error
        assert archive_source is not None
        # テストの中の小さなコピー。ここは to_thread に逃がす価値がない。
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

        # 2回目は取得しない（_download を呼ぶと例外になる細工をしておく）
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
        """**別の手段を探さない。** 展開できないなら失敗する。"""
        source = tmp_path / "engine.zip"
        make_archive(source, with_executable=True)
        monkeypatch.setattr(install_module, "_download", fake_download(archive_source=source))
        monkeypatch.delenv("SystemRoot", raising=False)
        monkeypatch.setattr(shutil, "which", lambda _: None)

        with pytest.raises(SetupError) as error:
            await install_engine(artifact_for(), tmp_path / "engines")
        assert error.value.reason == "tar_not_found"


class TestNetworkOptional:
    """**ユーザーが選ぶまで外部通信しない**（docs/architecture/setup.md 原則1）。

    静的検査。「呼ばれなければ通信しない」ことをコードの形で保証する
    （.claude/rules/tests.md「静的検査もテストである」）。
    """

    #: HTTP クライアントを持ってよいモジュールと、その理由。
    #: **増やすときは「いつ・どこへ通信するか」を必ず書く。**
    ALLOWED_HTTP: ClassVar[dict[str, str]] = {
        "setup/install.py": "エンジンの取得。ユーザーが選んだときだけ呼ばれる",
        "providers/tts/aivisspeech.py": "外部エンジン。127.0.0.1 のみ（下のテストで固定）",
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
        """エンジンの宛先を**設定可能にしない**。

        可能にすると「Lumi が任意のサーバに読み上げテキストを送る機能」になる。
        """
        module = Path(aivisspeech.__file__)
        urls = re.findall(r"https?://[^\"']*", module.read_text(encoding="utf-8"))
        assert urls, "URL の組み立て方が変わった。このテストを見直すこと"
        assert all(url.startswith("http://{HOST}:") for url in urls), urls
        assert aivisspeech.HOST == "127.0.0.1"

    def test_the_installer_is_only_reachable_through_the_coordinator(self) -> None:
        root = Path(__file__).resolve().parent.parent / "lumi"
        callers = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            if "install_engine(" in path.read_text(encoding="utf-8") and path.name != "install.py"
        ]
        assert callers == ["setup/coordinator.py"]
