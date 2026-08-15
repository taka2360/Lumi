from __future__ import annotations

from lumi import logging as lumi_logging


def test_configure_json_is_idempotent() -> None:
    lumi_logging.configure(console=False)
    lumi_logging.configure(console=False)
    log = lumi_logging.get_logger("test")
    log.info("hello", key="value")
