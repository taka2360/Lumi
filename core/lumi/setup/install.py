"""外部エンジンの取得・検証・展開・確定。

設計 → docs/architecture/setup.md §4-5

**この関数が呼ばれるのは、ユーザーが「取得する」を選んだ後だけ**である。
モジュールを import しただけでは HTTP クライアントを作らない
（原則1: ユーザーが選ぶまで外部通信しない）。

ファイルシステムの操作は `asyncio.to_thread` に逃がす。**イベントループを止めない**
（.claude/rules/python-core.md「ブロッキング I/O と推論はスレッドに逃がす」）。

失敗は必ず例外にする。**部分的にインストールされた状態を残さない**（原則3）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from lumi import logging as lumi_logging
from lumi.setup.engines import EngineArtifact, is_allowed_origin, is_allowed_redirect

log = lumi_logging.get_logger(__name__)

#: 1回の読み取り単位。
CHUNK_SIZE = 1024 * 256

#: 許すリダイレクトの回数。**無限に付いていかない。**
MAX_REDIRECTS = 5

#: 接続と読み取りのタイムアウト。取得は長時間かかるので read は緩め、connect は短く。
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

ProgressCallback = Callable[[float], Awaitable[None]]


class SetupError(RuntimeError):
    """セットアップの失敗。`reason` はユーザーに見せる短い識別子。"""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _find_executable(root: Path, name: str) -> Path | None:
    """展開後のツリーから実行体を探す。配布物の内部構造の変更に追随するため、名前で探す。"""
    if not root.is_dir():
        return None
    return next(root.rglob(name), None)


def _tar_executable() -> Path:
    """展開に使う bsdtar。**無ければ明示的に失敗する。別の手段を探さない。**"""
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidate = Path(system_root) / "System32" / "tar.exe"
        if candidate.is_file():
            return candidate
    found = shutil.which("tar")
    if found:
        return Path(found)
    raise SetupError("tar_not_found", "展開に使う tar が見つからない（Windows 10 1803 以降が必要）")


async def _download(
    client: httpx.AsyncClient,
    artifact: EngineArtifact,
    destination: Path,
    progress: ProgressCallback | None,
) -> None:
    """取得しながら**サイズと SHA-256 を検証する**。1つでも合わなければ例外。"""
    url = artifact.url
    if not is_allowed_origin(url):
        raise SetupError("origin_not_allowed", url)

    for hop in range(MAX_REDIRECTS + 1):
        if hop > 0 and not is_allowed_redirect(url):
            # リダイレクトで別の配布元へ連れて行かれるのを防ぐ。
            raise SetupError("redirect_not_allowed", url)

        async with client.stream("GET", url) as response:
            if response.is_redirect:
                next_request = response.next_request
                if next_request is None:
                    raise SetupError("redirect_without_location", url)
                url = str(next_request.url)
                continue

            if response.status_code != httpx.codes.OK:
                raise SetupError("http_error", f"{response.status_code} {url}")

            declared = response.headers.get("content-length")
            if declared is not None and int(declared) != artifact.size:
                raise SetupError("size_mismatch", f"content-length={declared}")

            digest = hashlib.sha256()
            written = 0
            reported = -1
            with destination.open("wb") as file:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    written += len(chunk)
                    if written > artifact.size:
                        # 期待より大きい。**最後まで読まずに打ち切る。**
                        raise SetupError("size_mismatch", f"received>{artifact.size}")
                    file.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        percent = written * 100 // artifact.size
                        if percent != reported:
                            reported = percent
                            await progress(written / artifact.size)

            if written != artifact.size:
                raise SetupError("size_mismatch", f"received={written} expected={artifact.size}")
            actual = digest.hexdigest()
            if actual != artifact.sha256:
                raise SetupError("hash_mismatch", f"sha256={actual}")
            return

    raise SetupError("too_many_redirects", url)


async def _extract(archive: Path, destination: Path) -> None:
    """bsdtar で展開する。

    配布物は多ボリューム形式の第1ボリューム（`.7z.001`）だが**単一ボリュームなので
    中身は完全な 7z ストリーム**であり、libarchive がそのまま読める。
    Python の 7z ライブラリ（LGPL）を Core に入れないための選択
    （docs/architecture/setup.md §5）。
    """
    tar = await asyncio.to_thread(_tar_executable)
    await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
    result = await asyncio.to_thread(
        subprocess.run,
        [str(tar), "-xf", str(archive), "-C", str(destination)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise SetupError("extract_failed", stderr[:500])


async def install_engine(
    artifact: EngineArtifact,
    engines_dir: Path,
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    """エンジンを取得してインストールし、実行体のパスを返す。

    **確定はディレクトリの rename 1手だけ。** そこに到達しなければ何も残らない。
    """
    final_dir = engines_dir / f"{artifact.name}-{artifact.version}"
    existing = await asyncio.to_thread(_find_executable, final_dir, artifact.executable_name)
    if existing is not None:
        log.info("setup.install.already_installed", path=str(existing))
        return existing

    await asyncio.to_thread(engines_dir.mkdir, parents=True, exist_ok=True)
    work_dir = engines_dir / f".tmp-{secrets.token_hex(8)}"
    await asyncio.to_thread(work_dir.mkdir)

    try:
        archive = work_dir / f"{artifact.name}-{artifact.version}.7z"
        log.info("setup.install.download.start", engine=artifact.name, version=artifact.version)
        # **ここで初めて外部へ接続する。** ユーザーの選択より前には到達しない。
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            await _download(client, artifact, archive, progress)
        log.info("setup.install.download.verified", sha256=artifact.sha256)

        extracted = work_dir / "extracted"
        await _extract(archive, extracted)

        executable = await asyncio.to_thread(_find_executable, extracted, artifact.executable_name)
        if executable is None:
            raise SetupError("executable_not_found", artifact.executable_name)

        # 展開物だけを確定させる（アーカイブは持ち越さない）。
        # **ここが唯一の確定操作**。これより前に失敗すれば何も残らない。
        relative = executable.relative_to(extracted)
        await asyncio.to_thread(extracted.rename, final_dir)
        log.info("setup.install.committed", path=str(final_dir))
        return final_dir / relative
    finally:
        # 成功でも失敗でも一時ディレクトリは残さない。
        await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)
