"""How often a fetch reports, and what it does with Ollama's per-layer byte counts.

**Pure functions, so tested as pure functions.** This logic used to be a closure inside
`install_llm_model`, reachable only by driving a whole model pull; the two failures it
exists to prevent both look like a hang, and neither is visible from an end state.
"""

from __future__ import annotations

from lumi.setup.progress import LayerProgress, throttle


async def test_updates_smaller_than_a_percent_are_dropped() -> None:
    sent: list[float] = []

    async def report(fraction: float) -> None:
        sent.append(fraction)

    gate = throttle(report)
    for fraction in (0.0, 0.005, 0.011, 0.012, 0.03):
        await gate(fraction)

    assert sent == [0.0, 0.011, 0.03]


async def test_the_last_update_always_gets_through() -> None:
    """**1.0 is what says the bar is finished.** Dropping it leaves it stuck near the end."""
    sent: list[float] = []

    async def report(fraction: float) -> None:
        sent.append(fraction)

    gate = throttle(report)
    await gate(0.999)
    await gate(1.0)

    assert sent == [0.999, 1.0]


def test_a_finished_small_layer_does_not_fill_the_bar() -> None:
    """**The failure this exists to prevent.** A metadata layer completes, and without
    this the bar reaches 100% and then freezes while the gigabytes arrive behind it.
    """
    layers = LayerProgress()

    assert layers.update(0, 100) == 0.0
    assert layers.update(100, 100) == 1.0

    # The real layer arrives: bigger total, so the bar restarts rather than staying full.
    assert layers.update(0, 10_000) == 0.0
    assert layers.update(5_000, 10_000) == 0.5


def test_a_layer_already_left_behind_is_ignored() -> None:
    layers = LayerProgress()
    assert layers.update(5_000, 10_000) == 0.5
    # A trailing report from the small layer that finished earlier.
    assert layers.update(100, 100) is None
    assert layers.update(6_000, 10_000) == 0.6


def test_a_same_sized_layer_starting_over_is_let_through() -> None:
    """**Otherwise nothing is ever sent again.** The new layer's fraction sits below the
    previous layer's high-water mark, and a gate comparing against it stays shut.
    """
    layers = LayerProgress()
    assert layers.update(9_000, 10_000) == 0.9

    restarted = layers.update(10, 10_000)
    assert restarted is not None and restarted < 0.9
    assert layers.update(2_000, 10_000) == 0.2
