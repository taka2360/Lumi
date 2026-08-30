"""Choosing which local model Lumi talks to, and getting it. **One pinned catalogue.**

Design → docs/architecture/setup.md §3b
Decision → docs/decisions/ADR-023-llm-runtime-and-model-acquisition.md
Decision → docs/decisions/ADR-037-consented-ollama-model-pull.md

Ollama is detected, never installed (ADR-023) — but a running Ollama with no model is
as mute as no Ollama at all, and the difference is invisible until the first reply never
comes. So the model is its own question, with its own size on it.

**Nothing here pulls without an answer.** `/api/pull` is reached from exactly one method,
after a choice carrying the byte count the user agreed to (ADR-037).

## Selecting and pulling end the same way

Both write the chosen name to settings before anything else, because a model Lumi
fetches and then forgets it chose is a model it will not load. Both therefore fail the
same way when that write fails, and both hand off to the same "check it is really there"
step afterwards — which is the Provider's job, not this one's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace

from lumi import logging as lumi_logging
from lumi import settings
from lumi.providers.base import EngineRuntime
from lumi.setup.broadcast import SetupStateBroadcaster
from lumi.setup.ollama import (
    OLLAMA_MODELS,
    QWEN_35_9B,
    OllamaLocalModel,
    OllamaModelArtifact,
    OllamaPullError,
    OllamaTagsError,
    list_ollama_models,
    pull_ollama_model,
)
from lumi.setup.progress import LayerProgress
from lumi.setup.prompter import Prompter
from lumi.setup.state import LlmSetup, LlmSetupState
from lumi.transport.methods import (
    CHOICE_INSTALL,
    CHOICE_SELECT,
    COMPONENT_LLM_MODEL,
)

log = lumi_logging.get_logger(__name__)


class LlmModelChooser:
    """Asks which model, remembers the answer, and fetches it if it is not here."""

    __slots__ = ("_on_detected", "_on_selected", "_prompter", "_prompting", "_state")

    def __init__(self, state: SetupStateBroadcaster, prompter: Prompter) -> None:
        self._state = state
        self._prompter = prompter
        self._prompting = False
        self._on_selected: Callable[[str], Awaitable[None]] | None = None
        self._on_detected: Callable[[], None] | None = None

    @property
    def prompting(self) -> bool:
        """**A model question is open.** A re-check must not overwrite the state behind it."""
        return self._prompting

    def on_selected(self, handler: Callable[[str], Awaitable[None]]) -> None:
        self._on_selected = handler

    def on_detected(self, handler: Callable[[], None]) -> None:
        self._on_detected = handler

    def detected(self) -> None:
        """Ollama is up. **Whether the model is really there is the Provider's question**
        — this only says it is worth asking now.
        """
        if self._on_detected is not None:
            self._on_detected()

    async def _failed(self, model: str, reason: str) -> None:
        """**Ollama is up; the model is not.** `READY` is the runtime, not the model —
        collapsing the two would tell the user to start something already running.
        """
        await self._state.replace(
            llm=LlmSetup(
                state=LlmSetupState.MODEL_FAILED,
                runtime=EngineRuntime.READY,
                model=model,
                reason=reason,
            )
        )

    async def ask(self) -> None:
        """Separately asks consent for a pinned, size-labelled Ollama model pull."""
        if self._prompting:
            return
        self._prompting = True
        retry = self._state.snapshot.llm.state is LlmSetupState.MODEL_FAILED
        detail = self._state.snapshot.llm.reason if retry else None
        try:
            while True:
                current = OLLAMA_MODELS.get(self._state.snapshot.llm.model or "", QWEN_35_9B)
                local_models = await self._local_models()
                local_by_name = {model.name: model for model in local_models}
                result = await self._prompter.ask(
                    {
                        "component": COMPONENT_LLM_MODEL,
                        "retry": retry,
                        "reason": detail,
                        "model": (
                            local_by_name[current.name].to_payload()
                            if current.name in local_by_name
                            else current.to_payload()
                        ),
                        "alternatives": self._options_payload(current.name, local_models),
                    }
                )
                if result is None:
                    return

                choice = result.payload.get("choice") if result.ok else None
                selected_name = result.payload.get("model")
                if choice == CHOICE_SELECT:
                    local = local_by_name.get(
                        selected_name if isinstance(selected_name, str) else ""
                    )
                    if local is None:
                        await self._failed(current.name, "unknown_model")
                        retry = True
                        detail = "unknown_model"
                        continue
                    await self.select_local(local)
                    if self._state.snapshot.llm.state is not LlmSetupState.MODEL_FAILED:
                        return
                    retry = True
                    detail = self._state.snapshot.llm.reason
                    continue
                if choice != CHOICE_INSTALL:
                    return
                artifact = OLLAMA_MODELS.get(
                    selected_name if isinstance(selected_name, str) else current.name
                )
                if artifact is None:
                    await self._failed(current.name, "unknown_model")
                else:
                    await self.pull(artifact)
                if self._state.snapshot.llm.state is not LlmSetupState.MODEL_FAILED:
                    return
                retry = True
                detail = self._state.snapshot.llm.reason
        finally:
            self._prompting = False
            self._state.asking(False)
            await self._state.publish()

    async def _local_models(self) -> tuple[OllamaLocalModel, ...]:
        """Reads local models for the choice screen; a transient catalog error is non-fatal."""
        try:
            return await list_ollama_models()
        except OllamaTagsError as error:
            log.info("setup.ollama_model.catalog_unavailable", detail=str(error))
            return ()

    def _options_payload(
        self, current_name: str, local_models: tuple[OllamaLocalModel, ...]
    ) -> list[dict[str, object]]:
        """Combines local models with the fixed download catalog without duplicates."""
        options: list[dict[str, object]] = []
        seen = {current_name}
        for local in local_models:
            if local.name in seen:
                continue
            seen.add(local.name)
            options.append(local.to_payload())
        for artifact in OLLAMA_MODELS.values():
            if artifact.name in seen:
                continue
            seen.add(artifact.name)
            options.append(artifact.to_payload())
        return options

    async def select_local(self, model: OllamaLocalModel) -> None:
        """Selects a model already present locally, without invoking `/api/pull`."""
        if self._on_selected is None:
            await self._failed(model.name, "model_selection_unavailable")
            return
        try:
            await self._on_selected(model.name)
        except (settings.SettingsError, OSError) as error:
            log.warning(
                "setup.ollama_model.selection_failed",
                model=model.name,
                reason=type(error).__name__,
            )
            await self._failed(model.name, "settings_save_failed")
            return
        await self._state.replace(
            llm=LlmSetup(
                state=LlmSetupState.DETECTED,
                runtime=EngineRuntime.STARTING,
                model=model.name,
                reason="model_checking",
            )
        )
        self.detected()

    async def pull(self, artifact: OllamaModelArtifact) -> None:
        """Asks Ollama's fixed local API to pull one consented, allowlisted model."""
        if self._on_selected is None:
            await self._failed(artifact.name, "model_selection_unavailable")
            return

        try:
            await self._on_selected(artifact.name)
        except (settings.SettingsError, OSError) as error:
            log.warning(
                "setup.ollama_model.selection_failed",
                model=artifact.name,
                reason=type(error).__name__,
            )
            await self._failed(artifact.name, "settings_save_failed")
            return
        await self._state.replace(
            llm=LlmSetup(
                state=LlmSetupState.MODEL_INSTALLING,
                runtime=EngineRuntime.READY,
                model=artifact.name,
                progress=0.0,
                completed_bytes=0,
                total_bytes=artifact.size_bytes,
            )
        )

        layers = LayerProgress()

        async def progress(completed: int, total: int) -> None:
            fraction = layers.update(completed, total)
            if fraction is None:
                return
            await self._state.replace(
                llm=replace(
                    self._state.snapshot.llm,
                    progress=fraction,
                    completed_bytes=completed,
                    total_bytes=total,
                )
            )

        try:
            await pull_ollama_model(artifact, progress=progress)
        except OllamaPullError as error:
            log.warning(
                "setup.ollama_model.failed",
                model=artifact.name,
                reason=error.reason,
                detail=error.detail,
            )
            await self._failed(artifact.name, error.reason)
            return
        except asyncio.CancelledError:
            await self._failed(artifact.name, "cancelled")
            raise

        await self._state.replace(
            llm=LlmSetup(
                state=LlmSetupState.DETECTED,
                runtime=EngineRuntime.STARTING,
                model=artifact.name,
                reason="model_checking",
            )
        )
        self.detected()
