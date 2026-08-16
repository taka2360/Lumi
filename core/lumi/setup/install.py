"""Fetching, verifying, extracting, and committing external engines.

Design → docs/architecture/setup.md §4-5

**This function is called only after the user has chosen to "fetch."**
Merely importing the module never creates an HTTP client (Principle 1: no external
communication until the user chooses to).

Filesystem operations are offloaded to `asyncio.to_thread`. **Never blocks the event
loop** (.claude/rules/python-core.md "Blocking I/O and inference are offloaded to threads").

Failures always become exceptions. **A partially installed state is never left
behind** (Principle 3).
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

#: The unit size for a single read.
CHUNK_SIZE = 1024 * 256

#: The number of redirects allowed. **Never follows indefinitely.**
MAX_REDIRECTS = 5

# : Connect and read timeouts. Fetching can take a long time, so read is generous while connect
# stays short.
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

ProgressCallback = Callable[[float], Awaitable[None]]


class SetupError(RuntimeError):
    """A setup failure. `reason` is a short identifier shown to the user."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _find_executable(root: Path, name: str) -> Path | None:
    """Finds the executable in the extracted tree. Searched by name so it tracks changes to the
    distributable's internal structure.
    """
    if not root.is_dir():
        return None
    return next(root.rglob(name), None)


def _tar_executable() -> Path:
    """The bsdtar used for extraction. **Fails explicitly if missing. No alternative is sought.**"""
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
    """Verifies **size and SHA-256 while fetching**. Raises if even one doesn't match."""
    url = artifact.url
    if not is_allowed_origin(url):
        raise SetupError("origin_not_allowed", url)

    for hop in range(MAX_REDIRECTS + 1):
        if hop > 0 and not is_allowed_redirect(url):
            # Prevents being redirected to a different distribution source.
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
                        # Larger than expected. **Aborted before reading to the end.**
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
    """Extracts with bsdtar.

    The distributable is the first volume of a multi-volume format (`.7z.001`), but
    **since it's actually a single volume, the content is a complete 7z stream**,
    which libarchive can read directly. This choice keeps a Python 7z library
    (LGPL) out of Core (docs/architecture/setup.md §5).
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
    """Fetches and installs the engine, returning the executable's path.

    **Commitment is a single directory rename.** Nothing is left behind unless that
    step is reached.
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
        # **This is the first point external connection happens.** Never reached before the user's
        # choice.
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            await _download(client, artifact, archive, progress)
        log.info("setup.install.download.verified", sha256=artifact.sha256)

        extracted = work_dir / "extracted"
        await _extract(archive, extracted)

        executable = await asyncio.to_thread(_find_executable, extracted, artifact.executable_name)
        if executable is None:
            raise SetupError("executable_not_found", artifact.executable_name)

        # Only the extracted contents are committed (the archive is not carried over).
        # **This is the sole commit operation.** Nothing is left behind if it fails before this.
        relative = executable.relative_to(extracted)
        await asyncio.to_thread(extracted.rename, final_dir)
        log.info("setup.install.committed", path=str(final_dir))
        return final_dir / relative
    finally:
        # The temp directory is never left behind, whether this succeeds or fails.
        await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)
