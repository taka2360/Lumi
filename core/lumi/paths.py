"""ユーザーデータと同梱物の置き場所。

**1箇所に閉じ込める。** 散らすと「全部消したい」に応えられなくなる
（docs/roadmap.md Phase 2 の 🔴 プライバシー項目 #5）。

Phase 0 で使うのは `engines_dir` と `setup_state_file` だけ。
記憶 DB / 監査ログの場所は Phase 2（`contracts/privacy.md` を書いてから）決める。

**ユーザーデータ（消せる）と同梱物（読み取り専用）は別物。** 前者は `data_dir()` の下、
後者は `content_dir()` の下にあり、消去の対象が違う。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: 開発時やテストで同梱物の場所を差し替える
CONTENT_DIR_ENV = "LUMI_CONTENT_DIR"


def data_dir() -> Path:
    """Lumi のユーザーデータのルート。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Lumi"
    # Windows 以外は XDG に寄せる（Phase 0 の対象外だが、パスの決定を分岐させない）。
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "lumi"


def content_dir() -> Path:
    """Content Pack の置き場所。**同梱物であってユーザーデータではない。**

    固めた実行体では PyInstaller の展開先、開発時はリポジトリ root の `content/`。
    **存在の確認はしない** — 無いことを見つけるのは読み込み側の仕事であり、
    ここで握り潰すと「なぜ人格が無いのか」が分からなくなる。
    """
    override = os.environ.get(CONTENT_DIR_ENV)
    if override:
        return Path(override)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "content"
    return Path(__file__).resolve().parents[2] / "content"


def default_character_dir() -> Path:
    """既定のキャラクター（Content Pack）→ docs/architecture/extension.md §9"""
    return content_dir() / "characters" / "lumi"


def engines_dir() -> Path:
    """外部エンジンのインストール先 → docs/architecture/setup.md §5"""
    return data_dir() / "engines"


def models_dir() -> Path:
    """実行時に取得するモデルの置き場所（STT など）→ ADR-023

    **配布物には入らない。** 数百 MB あり、R1（インストーラサイズ）に直撃するため、
    同意を得てから取得する。
    """
    return data_dir() / "models"


def setup_state_file() -> Path:
    """初回セットアップに「答えたか」を覚えておくファイル。

    設定の保存形式そのものは未確定（roadmap 未確定事項 #9 / Phase 1）。
    **ここでは「もう聞いた」1点だけを持つ。** 汎用の設定ストアにしない。
    """
    return data_dir() / "setup.json"
