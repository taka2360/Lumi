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

from lumi import paths as paths_module
from lumi.setup.install import SetupError, install_stt_model, is_model_installed
from lumi.setup.models import (
    ALLOWED_ORIGIN_PREFIX,
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
    for model in STT_MODELS.values():
        url = model.url_for(model.files[0])
        assert f"/resolve/{model.revision}/" in url, model.name
        assert "/main/" not in url, model.name


def test_every_pinned_file_carries_a_size_and_a_hash() -> None:
    for model in STT_MODELS.values():
        for file in model.files:
            assert file.size > 0, file.name
            assert len(file.sha256) == 64, file.name


def test_the_origin_is_restricted() -> None:
    assert is_allowed_origin("https://huggingface.co/Systran/faster-whisper-small/resolve/x/y")
    assert not is_allowed_origin("https://evil.example.com/model.bin")
    assert not is_allowed_origin("http://huggingface.co/a/b")


def test_the_origin_prefix_ends_at_the_authority_boundary() -> None:
    """★ **The trailing `/` in the prefix is what makes a `startswith` check sound.**

    The authority ends at the first `/` after `//`, so pinning through that slash pins the
    host exactly. Drop it from `ALLOWED_ORIGIN_PREFIX` and both of these start passing,
    with `_download` sending its first request to the attacker before any digest is checked.
    """
    assert ALLOWED_ORIGIN_PREFIX.endswith("/"), "権威部の終端まで固定していないと下が通る"
    assert not is_allowed_origin("https://huggingface.co.evil.example/a")
    assert not is_allowed_origin("https://huggingface.co@evil.example/a")
    assert not is_allowed_origin("https://huggingface.co:443/a")


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


async def test_a_connection_that_dies_mid_download_says_so(tmp_path: Path) -> None:
    """★ Regression (observed 2026-08-20): **this is the failure the user actually hit.**

    The 480 MB fetch dropped partway, and because nothing turned `httpx`'s exception into a
    reason, the setup panel said "an unexpected error occurred" — for the one failure where
    pressing retry is the whole fix. It was reported as `unexpected_error`, offered no
    guidance, and the very next attempt succeeded.

    Serving one file and then dying is the realistic shape: the model is several files, and
    the connection rarely gives out on the first one.
    """
    model = artifact()

    def serve_then_drop(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("config.json"):
            return httpx.Response(200, content=CONFIG)
        raise httpx.RemoteProtocolError("Server disconnected without sending a response")

    with pytest.raises(SetupError) as error:
        await _install(model, tmp_path, serve_then_drop)

    assert error.value.reason == "network_unreachable"
    assert not is_model_installed(model, tmp_path)
    remaining = await asyncio.to_thread(lambda: list(tmp_path.iterdir()))
    assert remaining == [], "一時ディレクトリが残っている"


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


async def test_an_incomplete_directory_is_repaired_by_reinstalling(tmp_path: Path) -> None:
    """★ Regression: **a retry could never repair a half-finished install.**

    `rename` refuses a destination that already exists, so a cancelled fetch (or a
    hand-edited directory) made every subsequent attempt re-download the whole model and
    then fail at the last step — with nothing telling the user that a directory they were
    never shown was the reason.
    """
    model = artifact()
    directory = model_directory(model, tmp_path)
    directory.mkdir(parents=True)
    (directory / "config.json").write_bytes(CONFIG)
    (directory / "model.bin").write_bytes(b"truncated")
    assert not is_model_installed(model, tmp_path)

    installed = await _install(
        model, tmp_path, serve({"config.json": CONFIG, "model.bin": WEIGHTS})
    )

    assert installed == directory
    assert is_model_installed(model, tmp_path)
    assert (directory / "model.bin").read_bytes() == WEIGHTS


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


class TestModelLocation:
    """★ Regression (observed 2026-08-17): **the setup panel offered to fetch a model
    that was already installed.**

    The fetcher looked under `models/` and the Provider under `models/whisper/`. Nothing
    failed — the consent question simply came back on a machine that had everything it
    needed, which is the kind of wrong nobody reports as a bug.
    """

    def test_speech_models_have_their_own_directory(self) -> None:
        """Models are deleted per kind. **Replacing the speech model has nothing to do
        with an embedding model** (Phase 2).
        """
        assert paths_module.stt_models_dir().parent == paths_module.models_dir()

    def test_nobody_builds_the_path_by_hand(self) -> None:
        """**One definition, both call sites.** A path assembled independently in two
        places is a path that will drift by one segment.
        """
        root = Path(__file__).resolve().parents[1] / "lumi"
        offenders = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            # `paths.py` is where the one definition lives
            if path.name != "paths.py" and 'models_dir() / "whisper"' in path.read_text("utf-8")
        ]
        assert offenders == [], f"paths.stt_models_dir() を使うこと: {offenders}"

    def test_the_fetched_model_is_the_one_the_provider_will_look_for(self) -> None:
        """**The two ends of the same decision.**

        `SetupCoordinator` fetches what `settings.stt_model` selects; `AgentRuntime`
        constructs the provider from the same setting. Drift between them fails the way a
        missing model fails — the setup panel offers to fetch something that is already on
        disk, and nothing errors (the same shape of bug as the directory drift observed
        2026-08-17).
        """
        from lumi.settings import KEYS
        from lumi.setup.coordinator import DEFAULT_STT_ARTIFACT

        _variable, default = KEYS["stt_model"]
        assert default == DEFAULT_STT_ARTIFACT.name
        assert default in STT_MODELS

    def test_an_override_selects_the_artifact_setup_will_fetch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """★ Regression: **`LUMI_STT_MODEL=small` left Lumi deaf while the screen said ready.**

        Setup checked and fetched a fixed `large-v3-turbo` while the Provider was built from
        the setting. STT read `installed`, boot reached `ready`, and every utterance died in
        a log line — for exactly the users the lighter model exists for (ADR-027).
        """
        from lumi.setup.coordinator import selected_stt_artifact

        monkeypatch.setattr(paths_module, "settings_file", lambda: tmp_path / "settings.json")

        assert selected_stt_artifact({"LUMI_STT_MODEL": "small"}) is STT_MODELS["small"]
        # Not pinned: **nothing is fetched in its place** — substituting is the whole bug
        assert selected_stt_artifact({"LUMI_STT_MODEL": "tiny"}) is None

    def test_the_fetcher_and_the_provider_are_given_the_same_root(self) -> None:
        """The fetcher (`SetupCoordinator`) and the reader (`FasterWhisperProvider`) have
        to be handed the identical directory, or one of them is always wrong.
        """
        sources = {
            name: (Path(__file__).resolve().parents[1] / "lumi" / name).read_text(encoding="utf-8")
            for name in ("setup/coordinator.py", "agent/runtime.py")
        }
        for name, text in sources.items():
            assert "paths.stt_models_dir()" in text, f"{name} が共通の定義を使っていない"
