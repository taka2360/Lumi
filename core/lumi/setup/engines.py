"""取得する外部エンジンのピン留めと、URL の検証。**純粋**。

唯一の定義場所は docs/architecture/setup.md §3-4。ここはその実体。

**取得先を設定可能にしない。** ユーザーが URL を差し替えられるなら、それは
「Lumi が任意の実行体を落としてくる機能」であって、セットアップではない。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class EngineArtifact:
    """ピン留めした配布物。**この4つ（version / url / size / sha256）が検証の根拠**。"""

    name: str
    display_name: str
    version: str
    url: str
    #: バイト数。ピン留めした値と一致しなければインストールしない。
    size: int
    #: 取得したバイト列の SHA-256。ピン留めした値と一致しなければインストールしない。
    sha256: str
    license_name: str
    license_url: str
    #: エンジンが既定で listen するポート。
    default_port: int
    #: 展開後のツリーから探す実行体の名前。
    executable_name: str


#: AivisSpeech Engine（**Engine のみ。GUI アプリの AivisSpeech は取得しない**）。
#:
#: sha256 は GitHub Release Asset API の `digest` から採った〔2026-08-15〕。
#: **これが保証するのは「ピン留め時点の配布物と同一であること」だけ**である
#: （docs/architecture/setup.md §4「保証しないこと」）。
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

#: 取得の起点として許す URL の接頭辞。**ここ以外から取らない。**
ALLOWED_ORIGIN_PREFIX = "https://github.com/Aivis-Project/AivisSpeech-Engine/releases/download/"

#: リダイレクト先として許すホスト。GitHub の Release Asset は CDN に飛ぶ。
#: **ホストを固定することで、リダイレクトで別の配布元に連れて行かれることを防ぐ。**
ALLOWED_REDIRECT_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
)


def is_allowed_origin(url: str) -> bool:
    """取得の起点として妥当か。"""
    return url.startswith(ALLOWED_ORIGIN_PREFIX)


def is_allowed_redirect(url: str) -> bool:
    """リダイレクト先として妥当か。**https かつホストが allowlist にあること。**"""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    # ポート指定つき（host:443）でも host だけを見る。
    return parts.hostname in ALLOWED_REDIRECT_HOSTS
