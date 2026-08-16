"""Structured logging.

As of Phase 0, "Shell captures stdout/stderr into logs" (docs/interfaces/shell.md), so
one JSON object per line is written to stdout by default. Color output only kicks in
when a human is reading it directly (dev time).

SLO measurement (p50/p95/p99, unaccounted_ms) is Phase 1. Only the plumbing is set up here.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure(*, level: str = "INFO", console: bool | None = None) -> None:
    """Initializes structlog. Called exactly once at process startup.

    When `console=None`, output is human-readable if stdout is a TTY, otherwise JSON.
    When launched as Shell's sidecar, stdout is a pipe, so it becomes JSON.
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
