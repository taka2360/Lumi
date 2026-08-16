"""Pinning for STT models to fetch, and URL validation. **Pure.**

The single source of definition is docs/architecture/setup.md §3b. This is its concrete
implementation. Decision → ADR-023.

## Why this isn't `EngineArtifact`

An engine is one archive; a model is **a directory of files**. Pinning has to be per-file, and
nothing gets extracted. Forcing both into one type would mean a "sometimes an archive, sometimes
not" branch inside the fetch path — the kind of branch that quietly stops being tested.

## The revision is pinned, not the branch

Resolving through `main` means **the same URL can serve different bytes later**. Pinning the
commit is what makes "the same as when we pinned it" a statement about anything at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

#: Where models may be fetched from. **Never fetched from anywhere else.**
ALLOWED_ORIGIN_PREFIX: Final = "https://huggingface.co/"

#: Hosts allowed as a redirect destination, matched exactly.
ALLOWED_REDIRECT_HOSTS: Final = frozenset({"huggingface.co"})

#: Domains whose subdomains are allowed as a redirect destination.
#:
#: **HuggingFace's storage CDN uses a per-region hostname** (`us.aws.cdn.hf.co`,
#: `cas-bridge.xethub.hf.co`, ...) that is not published as a fixed list and that differs by
#: where the user is [2026-08-16 observed]. An exact list would fail on someone else's machine
#: — fail-closed, but as an unusable feature rather than a safe one.
#:
#: **What the host check is and isn't**: it is defence in depth. The actual integrity guarantee
#: is the pinned SHA-256, and **a wrong host cannot serve us wrong bytes — only fail.**
#: Narrowing to the distributor's own domains keeps the redirect from leaving HuggingFace at all.
ALLOWED_REDIRECT_SUFFIXES: Final = ("cdn.hf.co", "xethub.hf.co", "huggingface.co")


@dataclass(frozen=True, slots=True)
class ModelFile:
    """One file of a model. **Size and SHA-256 are both pinned.**"""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """A pinned model. **Nothing is installed unless every file verifies.**"""

    #: What `FasterWhisperProvider(model_size=...)` is given
    name: str
    repo: str
    #: git commit sha. **Not a branch** — a branch's contents can change under the same URL
    revision: str
    files: tuple[ModelFile, ...]
    license_name: str
    license_url: str

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)

    def url_for(self, file: ModelFile) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{file.name}"


#: faster-whisper small [Provisional].
#:
#: Chosen as the starting point for Japanese: `base` drops too much accuracy, and `medium`
#: (~1.5 GB) blows the STT budget of 0.22 s (docs/architecture/audio.md §7).
#: **Revisit once the SLO has actually been measured.**
#:
#: The `model.bin` SHA-256 came from HuggingFace's LFS metadata; the rest were computed here
#: from one fetch [2026-08-16]. **All this guarantees is "identical to the distributable at
#: the time it was pinned"** (docs/architecture/setup.md §3b).
FASTER_WHISPER_SMALL: Final = ModelArtifact(
    name="small",
    repo="Systran/faster-whisper-small",
    revision="536b0662742c02347bc0e980a01041f333bce120",
    files=(
        ModelFile(
            name="config.json",
            size=2370,
            sha256="b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        ),
        ModelFile(
            name="model.bin",
            size=483_546_902,
            sha256="3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        ),
        ModelFile(
            name="tokenizer.json",
            size=2_203_239,
            sha256="fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        ),
        ModelFile(
            name="vocabulary.txt",
            size=459_861,
            sha256="34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
        ),
    ),
    license_name="MIT",
    license_url="https://huggingface.co/Systran/faster-whisper-small",
)

#: Fetchable models, by name. **Not configurable** — a user-supplied URL would turn this into
#: "a feature where Lumi downloads arbitrary files"
STT_MODELS: Final[dict[str, ModelArtifact]] = {FASTER_WHISPER_SMALL.name: FASTER_WHISPER_SMALL}


def model_directory(artifact: ModelArtifact, models_dir: Path) -> Path:
    """Where the model lands. **Pure** — computing it never touches the filesystem.

    The revision is part of the name so that **re-pinning installs alongside** rather than
    over the top. A half-overwritten model directory is indistinguishable from a corrupt one.
    """
    return models_dir / f"{artifact.repo.split('/')[-1]}-{artifact.revision[:12]}"


def is_allowed_origin(url: str) -> bool:
    """Whether this is valid as a fetch origin."""
    return url.startswith(ALLOWED_ORIGIN_PREFIX)


def is_allowed_redirect(url: str) -> bool:
    """Whether this is valid as a redirect destination. **Must be https and on the allowlist.**

    Suffixes are matched on a label boundary. **`evil-cdn.hf.co.attacker.example` must not
    match `cdn.hf.co`** — a plain `endswith` would let it through.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    host = parts.hostname
    if host is None:
        return False
    if host in ALLOWED_REDIRECT_HOSTS:
        return True
    return any(host.endswith(f".{suffix}") for suffix in ALLOWED_REDIRECT_SUFFIXES)
