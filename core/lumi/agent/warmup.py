"""Bringing the inference engines up, and **deciding whether Lumi may come out.**

Design → docs/architecture/setup.md §2b / ADR-033 / ADR-034

Warming means the weights are in memory. Anything left cold is simply a bill handed to
the first reply (docs/interfaces/provider.md "`load()` は接続確認ではない"), and STT was
the one nobody was warming at all — its 2489 ms model build sat between the latency
spans and showed up only as `unaccounted_ms` (2321 ms observed 2026-08-18).

## Warming is also how the setup state gets settled

Detection looks at the disk and at 127.0.0.1; it cannot see `model_missing` (that needs
Ollama's API) and it cannot see whether an installed engine actually starts. **Whoever
finds out is the one who reports it**, so each `warm_*` here reports what it learned to
the Coordinator, and the boot phase is derived from those reports.

That makes this module the thing that decides whether the character appears at all
(ADR-034) and whether the microphone opens (ADR-033) — which is why it is not part of
`runtime.py`. Assembling a conversation and judging whether one is possible are
different jobs, and `runtime.py` says of itself that it holds no judgment.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from lumi import logging as lumi_logging
from lumi.providers.base import ProviderError, ProviderKind, ProviderNotConfigured
from lumi.providers.registry import ProviderRegistry
from lumi.setup.coordinator import SetupCoordinator
from lumi.setup.state import BootPhase, EngineRuntime, LlmSetupState, SttSetupState

log = lumi_logging.get_logger(__name__)


async def warm_all(
    providers: ProviderRegistry,
    setup: SetupCoordinator,
    model: str,
    *,
    on_ready: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Brings the engines up and **reports what each one turned out to be.**

    Sequential, not concurrent: the TTS engine saturates the GPU while it starts, and
    probing the LLM in the middle of that measures the contention rather than the LLM.

    **All three, not just the two that report state.** Warming means the weights are in
    memory — anything left cold is simply a bill handed to the first reply
    (docs/interfaces/provider.md "`load()` は接続確認ではない"), and STT was the one nobody
    was warming at all.

    **Listening starts last, and only if the phase actually reached `ready`** (ADR-034).
    It used to start right after the TTS engine, back when `ready` meant "the engine is
    up"; now it means all three work, and the microphone follows that same boundary
    rather than a weaker one (ADR-033). If anything is still missing the phase is
    `blocked`, and Lumi does not listen behind a screen that says it has not started.
    """
    await warm_tts(providers, setup)
    await warm_llm(providers, setup, model)
    await warm_stt(providers, setup)

    if on_ready is None:
        return
    if setup.boot is not BootPhase.READY:
        # **The same derivation the Stage was handed**, read back rather than re-decided
        # here. Two places deciding "is it ready" is how the screen and the microphone
        # come to disagree.
        log.info("conversation.not_started", boot=str(setup.boot))
        return
    await on_ready()


async def warm_llm(providers: ProviderRegistry, setup: SetupCoordinator, model: str) -> None:
    """Finds out whether the LLM can actually answer, and says so.

    **Detection alone cannot see `model_missing`** — that needs Ollama's API, which only
    the Provider talks to (docs/architecture/setup.md §2b). So the state is settled here,
    where the answer is known.

    **This report is what lets the character out** (ADR-034). A Lumi that hears and never
    answers is not a lesser Lumi, it is a broken-looking one, so the phase stays `blocked`
    until Ollama is there, running, and holding the configured model. What is missing and
    what to do about it is on screen the whole time.
    """
    try:
        await providers.get(ProviderKind.LLM)
    except ProviderNotConfigured as error:
        # Ollama answered, but the model isn't pulled. **A different instruction entirely**
        await setup.report_llm(LlmSetupState.MODEL_MISSING, reason=error.detail, model=model)
        return
    except ProviderError as error:
        # Not running (or answering abnormally). **Never reported as "not installed"** —
        # detection already established whether it is on the machine
        log.info("llm.warmup_failed", reason=error.reason, detail=error.detail)
        await setup.report_llm(LlmSetupState.NOT_CONFIGURED, reason=error.detail, model=model)
        return
    await setup.report_llm(LlmSetupState.DETECTED, reason=None, model=model)


async def warm_stt(providers: ProviderRegistry, setup: SetupCoordinator) -> None:
    """Builds the speech-recognition model **before** the first utterance needs it.

    **This was the `unaccounted_ms`.** `stt_ms` times `transcribe`, so the 2489 ms model
    build sat between the spans and showed up only as unattributed time (2321 ms observed
    2026-08-18) — the reserve's warning light lit by something that was never a mystery.

    Unlike the LLM, whether the model is present is decided by looking at the disk, which
    detection already did (docs/architecture/setup.md §2b). An uninstalled model is therefore
    not warmed; a failure to build an installed model is reported as `runtime: failed`
    while its acquisition state stays `installed`, so it cannot make the boot phase look ready.
    """
    if setup.state.stt.state is not SttSetupState.INSTALLED:
        return
    await setup.set_stt_runtime(EngineRuntime.STARTING)
    try:
        await providers.get(ProviderKind.STT)
    except ProviderError as error:
        log.warning("stt.warmup_failed", reason=error.reason, detail=error.detail)
        await setup.set_stt_runtime(EngineRuntime.FAILED)
        return
    await setup.set_stt_runtime(EngineRuntime.READY)


async def warm_tts(providers: ProviderRegistry, setup: SetupCoordinator) -> None:
    """Starts the TTS engine, and **reports the process state as it moves**.

    Two states that move independently (docs/architecture/setup.md "Never mix
    installation state and process state"): the Provider owns the process, the
    Coordinator owns the state the Stage sees. **Whoever starts the process is the one
    who has to report it**, or the two silently drift apart.

    Failing to start resolves to boot phase `blocked`, never `ready` (ADR-034): an
    engine that is installed and will not run is broken, and **a fetch that failed must
    never be able to read as a successful start.** The screen says which of the two it
    was, and offers what to do next.
    """
    if not providers.has(ProviderKind.TTS):
        # Not set up, or the fetch was declined. **Not broken**, so the state stays
        # `stopped` — it really isn't running, and nothing is starting it. Broadcast it
        # anyway: `stopped` is what moves the phase off `starting` and onto `blocked`.
        await setup.set_runtime(EngineRuntime.STOPPED)
        return

    await setup.set_runtime(EngineRuntime.STARTING)
    try:
        await providers.get(ProviderKind.TTS)
    except ProviderError as error:
        # Installed but won't start = broken. **Never leave it looking like it's still
        # starting.**
        log.warning("tts.warmup_failed", reason=error.reason, detail=error.detail)
        await setup.set_runtime(EngineRuntime.FAILED)
        return
    await setup.set_runtime(EngineRuntime.READY)
