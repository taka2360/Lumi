"""構造化ログ。

Phase 0 の時点では「Shell が stdout/stderr をログに落とす」（docs/interfaces/shell.md）ため、
既定では 1 行 1 JSON を stdout に吐く。人が直接読むとき（開発時）だけ色つきに切り替える。

SLO 計測（p50/p95/p99、unaccounted_ms）は Phase 1。ここでは経路だけ用意する。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure(*, level: str = "INFO", console: bool | None = None) -> None:
    """structlog を初期化する。プロセス起動時に一度だけ呼ぶ。

    console=None のとき、stdout が TTY なら人間向け、そうでなければ JSON にする。
    Shell のサイドカーとして起動された場合はパイプなので JSON になる。
    """
    if console is None:
        console = sys.stdout.isatty()

    renderer: Any = (
        structlog.dev.ConsoleRenderer() if console else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
