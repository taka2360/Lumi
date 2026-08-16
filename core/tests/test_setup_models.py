"""STT model pinning and installation. **docs/architecture/setup.md §3b / ADR-023.**

The point of these is fail-closed: **"almost installed" must never count as installed.**
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from lumi.setup.install import SetupError, install_stt_model, is_model_installed
from lumi.setup.models import (
    STT_MODELS,
    ModelArtifact,
    ModelFile,
    is_allowed_origin,
    is_allowed_redirect,
    model_directory,
)

CONFIG = b'{"model_type": "whisper"}'
WEIGHTS = b"\x00\x01\x02" * 100


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact() -> ModelArtifact:
    return ModelArtifact(
        name="test",
        repo="Systran/faster-whisper-test",
        revision="0123456789abcdef0123456789abcdef01234567",
        files=(
            ModelFile(name="config.json", size=len(CONFIG), sha256=digest(CONFIG)),
            ModelFile(name="model.bin", size=len(WEIGHTS), sha256=digest(WEIGHTS)),
        ),
        license_name="MIT",
        license_url="https://example.invalid",
    )


def serve(bodies: dict[str, bytes]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in bodies:
            return httpx.Response(404)
        return httpx.Response(200, content=bodies[name])

    return handler


# ── Pinning ──────────────────────────────────────────────────


def test_the_url_pins_the_revision_not_a_branch() -> None:
    """★ **`main` can serve different bytes later.**

    Pinning the commit is what makes "the same as when we pinned it" mean anything.
    """
    model = STT_MODELS["small"]
    url = model.url_for(model.files[0])
    assert f"/resolve/{model.revision}/" in url
    assert "/main/" not in url


def test_every_pinned_file_carries_a_size_and_a_hash() -> None:
    for model in STT_MODELS.values():
        for file in model.files:
            assert file.size > 0, file.name
            assert len(file.sha256) == 64, file.name


def test_the_origin_is_restricted() -> None:
    assert is_allowed_origin("https://huggingface.co/Systran/faster-whisper-small/resolve/x/y")
    assert not is_allowed_origin("https://evil.example.com/model.bin")
    assert not is_allowed_origin("http://huggingface.co/a/b")


def test_redirects_are_restricted_to_the_distributor_over_https() -> None:
    """The CDN host varies by region, so subdomains of HuggingFace's own domains are allowed."""
    assert is_allowed_redirect("https://cdn-lfs.huggingface.co/a/b")
    assert is_allowed_redirect("https://us.aws.cdn.hf.co/xet-bridge-us/x")
    assert is_allowed_redirect("https://cas-bridge.xethub.hf.co/x")
    assert not is_allowed_redirect("http://us.aws.cdn.hf.co/a/b")
    assert not is_allowed_redirect("https://evil.example.com/a/b")


def test_a_suffix_match_respects_the_label_boundary() -> None:
    """★ **A plain `endswith` would let an attacker-owned domain through.**

    `evil-cdn.hf.co.attacker.example` ends with neither `.cdn.hf.co` nor `.huggingface.co`,
    but `cdn.hf.co` appears inside it.
    """
    assert not is_allowed_redirect("https://evil-cdn.hf.co.attacker.example/a")
    assert not is_allowed_redirect("https://huggingface.co.evil.example/a")
    assert not is_allowed_redirect("https://notcdn.hf.co.evil.example/a")


def test_the_directory_name_includes_the_revision(tmp_path: Path) -> None:
    """**Re-pinning installs alongside, not over the top.**

    A half-overwritten model directory looks exactly like a corrupt one.
    """
    model = artifact()
    assert model.revision[:12] in model_directory(model, tmp_path).name


# ── Installation ─────────────────────────────────────────────


async def test_a_complete_fetch_is_committed(tmp_path: Path) -> None:
    model = artifact()
    bodies = {"config.json": CONFIG, "model.bin": WEIGHTS}

    installed = await _install(model, tmp_path, serve(bodies))

    assert (installed / "model.bin").read_bytes() == WEIGHTS
    assert is_model_installed(model, tmp_path)


async def test_a_hash_mismatch_installs_nothing(tmp_path: Path) -> None:
    """**Fail-closed.** One bad file means no model at all."""
    model = artifact()
    bodies = {"config.json": CONFIG, "model.bin": b"tampered" + WEIGHTS[8:]}

    with pytest.raises(SetupError) as error:
        await _install(model, tmp_path, serve(bodies))

    assert error.value.reason == "hash_mismatch"
    assert not is_model_installed(model, tmp_path)


async def test_nothing_is_left_behind_on_failure(tmp_path: Path) -> None:
    """A temp directory left behind would be mistaken for a usable model later."""
    model = artifact()

    with pytest.raises(SetupError):
        await _install(model, tmp_path, serve({"config.json": CONFIG}))

    remaining = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert remaining == []


async def test_reinstalling_is_a_no_op(tmp_path: Path) -> None:
    model = artifact()
    await _install(model, tmp_path, serve({"config.json": CONFIG, "model.bin": WEIGHTS}))

    # A handler that would fail if it were called again
    def refuse(_: httpx.Request) -> httpx.Response:
        raise AssertionError("再取得してはいけない")

    assert await _install(model, tmp_path, refuse) == model_directory(model, tmp_path)


async def test_progress_covers_the_whole_model(tmp_path: Path) -> None:
    """**The bar must not reset on every file** — that reads as "it restarted."""
    model = artifact()
    seen: list[float] = []

    async def progress(fraction: float) -> None:
        seen.append(fraction)

    await _install(
        model, tmp_path, serve({"config.json": CONFIG, "model.bin": WEIGHTS}), progress=progress
    )

    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


# ── "Almost installed" is not installed ──────────────────────


def test_a_missing_file_is_not_installed(tmp_path: Path) -> None:
    model = artifact()
    directory = model_directory(model, tmp_path)
    directory.mkdir(parents=True)
    (directory / "config.json").write_bytes(CONFIG)

    assert not is_model_installed(model, tmp_path)


def test_a_truncated_file_is_not_installed(tmp_path: Path) -> None:
    """★ **A half-written file is the realistic failure**, not a missing one.

    Reported as "the model isn't there" so the fix is to fetch it again.
    """
    model = artifact()
    directory = model_directory(model, tmp_path)
    directory.mkdir(parents=True)
    (directory / "config.json").write_bytes(CONFIG)
    (directory / "model.bin").write_bytes(WEIGHTS[:10])

    assert not is_model_installed(model, tmp_path)


async def _install(
    model: ModelArtifact,
    models_dir: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: object,
) -> Path:
    """Runs the real install path over a mock transport instead of the network."""
    return await install_stt_model(
        model,
        models_dir,
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )
