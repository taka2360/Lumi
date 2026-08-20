"""Pinning for external engines to fetch, and URL validation. **Pure.**

The single source of definition is docs/architecture/setup.md §3-4. This is its
concrete implementation.

**The fetch source is never configurable.** If the user could swap the URL, that
would be "a feature where Lumi downloads an arbitrary executable" — not setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class EngineArtifact:
    """A pinned distributable. **These four (version / url / size / sha256) are the basis of
    verification.**
    """

    name: str
    display_name: str
    version: str
    url: str
    #: Size in bytes. Never installed if it doesn't match the pinned value.
    size: int
    #: SHA-256 of the fetched bytes. Never installed if it doesn't match the pinned value.
    sha256: str
    license_name: str
    license_url: str
    #: The port the engine listens on by default.
    default_port: int
    #: The name of the executable to find in the extracted tree.
    executable_name: str


#: The AivisSpeech Engine (**Engine only. Never fetches the AivisSpeech GUI app**).
#:
#: The sha256 was taken from the GitHub Release Asset API's `digest` [2026-08-15].
#: **All this guarantees is "identical to the distributable at the time it was
#: pinned"** (docs/architecture/setup.md §4 "What this does not guarantee").
AIVISSPEECH_ENGINE = EngineArtifact(
    name="aivisspeech",
    display_name="AivisSpeech Engine",
    version="1.2.0",
    url=(
        "https://github.com/Aivis-Project/AivisSpeech-Engine/releases/download/"
        "1.2.0/AivisSpeech-Engine-Windows-x64-1.2.0.7z.001"
    ),
    size=216_525_495,
    sha256="bfbceba2e14dc7f23c7f3695f9ac0381baf91b15d6544e98384574eaadd271f3",
    license_name="LGPL-3.0",
    license_url="https://github.com/Aivis-Project/AivisSpeech-Engine/blob/master/LICENSE",
    default_port=10101,
    executable_name="run.exe",
)

#: The URL prefix allowed as a fetch origin. **Never fetched from anywhere else.**
ALLOWED_ORIGIN_PREFIX = "https://github.com/Aivis-Project/AivisSpeech-Engine/releases/download/"

#: Hosts allowed as a redirect destination. GitHub's Release Asset redirects to a CDN.
#: **Pinning the host prevents being redirected to a different distribution source.**
ALLOWED_REDIRECT_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)


def is_allowed_origin(url: str) -> bool:
    """Whether this is valid as a fetch origin."""
    return url.startswith(ALLOWED_ORIGIN_PREFIX)


def is_allowed_redirect(url: str) -> bool:
    """Whether this is valid as a redirect destination. **Must be https and the host must be on the
    allowlist.**
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    # Even with a port specified (host:443), only the host is checked.
    return parts.hostname in ALLOWED_REDIRECT_HOSTS
