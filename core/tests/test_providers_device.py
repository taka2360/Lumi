"""Which device inference runs on. **DESIGN.md §7 / ADR-025.**

The point is that **the answer is reported, never silently taken.** A Provider that quietly
falls back to CPU turns a 15x latency regression into a mystery.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lumi.providers import device as device_module
from lumi.providers.device import DeviceChoice, resolve


@pytest.fixture(autouse=True)
def _clear_probe_cache() -> Iterator[None]:
    """The probe is cached for the process. **Tests must not inherit each other's answer.**"""
    device_module.cuda_available.cache_clear()
    yield
    device_module.cuda_available.cache_clear()


def use(monkeypatch: pytest.MonkeyPatch, *, cuda: bool) -> None:
    monkeypatch.setattr(device_module, "cuda_available", lambda: cuda)


def test_auto_takes_the_gpu_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, cuda=True)
    assert resolve(DeviceChoice.AUTO) is DeviceChoice.CUDA


def test_auto_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """**A machine without an NVIDIA GPU still runs Lumi.**

    "Can't speak but isn't broken" is a legitimate state (setup.md §1); "won't start"
    is not.
    """
    use(monkeypatch, cuda=False)
    assert resolve(DeviceChoice.AUTO) is DeviceChoice.CPU


def test_cpu_is_honoured_even_with_a_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The escape hatch for small-VRAM machines** (ADR-025). A setting outranks detection."""
    use(monkeypatch, cuda=True)
    assert resolve(DeviceChoice.CPU) is DeviceChoice.CPU


def test_requesting_a_missing_gpu_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for CUDA on a machine without it is a mistake, **not a reason to refuse to run**."""
    use(monkeypatch, cuda=False)
    assert resolve(DeviceChoice.CUDA) is DeviceChoice.CPU


def test_resolve_never_returns_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers branch on the result. **`AUTO` leaking through would be a silent third case.**"""
    for cuda in (True, False):
        use(monkeypatch, cuda=cuda)
        assert resolve(DeviceChoice.AUTO) is not DeviceChoice.AUTO


def test_a_plain_string_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings and env vars arrive as strings."""
    use(monkeypatch, cuda=True)
    assert resolve("cpu") is DeviceChoice.CPU


def test_an_unknown_device_is_refused() -> None:
    """**Never guess.** A typo'd setting must not silently become CPU."""
    with pytest.raises(ValueError, match="tpu"):
        resolve("tpu")


def test_the_probe_survives_a_broken_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """**A failing probe means "no GPU", not a crash at startup.**"""

    class Broken:
        @staticmethod
        def get_cuda_device_count() -> int:
            raise RuntimeError("driver mismatch")

    monkeypatch.setitem(__import__("sys").modules, "ctranslate2", Broken)
    assert device_module.cuda_available() is False
