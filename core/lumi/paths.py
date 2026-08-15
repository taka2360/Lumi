"""ユーザーデータの置き場所。

**1箇所に閉じ込める。** 散らすと「全部消したい」に応えられなくなる
（docs/roadmap.md Phase 2 の 🔴 プライバシー項目 #5）。

Phase 0 で使うのは `engines_dir` と `setup_state_file` だけ。
記憶 DB / 監査ログの場所は Phase 2（`contracts/privacy.md` を書いてから）決める。
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Lumi のユーザーデータのルート。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Lumi"
    # Windows 以外は XDG に寄せる（Phase 0 の対象外だが、パスの決定を分岐させない）。
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "lumi"


def engines_dir() -> Path:
    """外部エンジンのインストール先 → docs/architecture/setup.md §5"""
    return data_dir() / "engines"


def setup_state_file() -> Path:
    """初回セットアップに「答えたか」を覚えておくファイル。

    設定の保存形式そのものは未確定（roadmap 未確定事項 #9 / Phase 1）。
    **ここでは「もう聞いた」1点だけを持つ。** 汎用の設定ストアにしない。
    """
    return data_dir() / "setup.json"
