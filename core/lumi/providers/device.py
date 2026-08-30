"""Which device an inference Provider should run on.

Strategy -> DESIGN.md §7 "GPU / VRAM strategy" / decision -> ADR-025

## Why detection lives here rather than in each Provider

Every Provider needs the same answer to the same question, and **the answer has to be
reported, not just used**. A Provider that silently falls back to CPU turns a 6x latency
regression into a mystery ("Lumi got slow on my other PC").

## What this does not do

**No VRAM budgeting.** That is `ModelResourceManager` (Phase 5). This only answers
"is there a CUDA device at all", which is the question Phase 1 actually has.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)


class DeviceChoice(StrEnum):
    AUTO = "auto"
    CUDA = "cuda"
    CPU = "cpu"


@lru_cache(maxsize=1)
def cuda_available() -> bool:
    """Whether CTranslate2 can see a CUDA device.

    Asked through CTranslate2 rather than `nvidia-smi` because **what matters is whether
    the library we actually use can reach the GPU**, not whether one is installed. A driver
    that is present but unusable should read as "no GPU" here.
    """
    try:
        import ctranslate2
    except ImportError:  # pragma: no cover - unreachable if the dependency is present
        return False
    try:
        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception as error:
        log.info("device.cuda_probe_failed", error=str(error))
        return False


def resolve(choice: DeviceChoice | str = DeviceChoice.AUTO) -> DeviceChoice:
    """Resolve to `CUDA` or `CPU`. **Never returns `AUTO`.**

    **Falling back is logged.** Running on CPU is a legitimate state — running on CPU
    *without anyone knowing* is how "it's slower on this machine" becomes unexplainable.
    """
    requested = DeviceChoice(choice)
    if requested is DeviceChoice.CPU:
        return DeviceChoice.CPU
    if cuda_available():
        return DeviceChoice.CUDA
    if requested is DeviceChoice.CUDA:
        # Asked for explicitly and not there. **Say so** rather than quietly obeying
        log.warning("device.cuda_requested_but_absent")
    else:
        log.info("device.no_cuda", detail="Running on CPU. SLO assumes GPU configuration")
    return DeviceChoice.CPU
