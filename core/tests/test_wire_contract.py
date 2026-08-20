"""Core's wire constants match `docs/contracts/wire.json`.

Contract and rules → docs/contracts/wire.md / ADR-022

Turns **"fixing only one side never errors"** into a test failure. Lumi's
implementation falls back to a safe default for unknown values (`?? null` /
`unknown_method`), so drift shows up as **silent degradation**, not an exception.
This is where it's caught.

The same cross-check is also done by the Stage (`stage/src/core/wire.test.ts`) and
Shell (`shell/src-tauri/src/wire_contract.rs`).
**Missing it in even one language lets only that language quietly drift.**
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from lumi.character import Emotion
from lumi.providers.tts.viseme import Viseme
from lumi.settings import Source
from lumi.setup.state import (
    BootPhase,
    EngineRuntime,
    LlmSetupState,
    SttSetupState,
    TtsSetupState,
)
from lumi.transport.methods import (
    CHARACTER_MODEL_REASONS,
    CHOICE_INSTALL,
    CHOICE_SKIP,
    COMPONENT_STT,
    COMPONENT_TTS,
    INBOUND_METHODS,
    STAGE_METHODS,
)
from lumi.transport.protocol import NAMESPACE_BY_ROLE, PROTOCOL_VERSION, Role

# : The repo's `docs/contracts/wire.json`. **Never read at runtime** (docs aren't bundled in the
# distributable).
WIRE_PATH = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "wire.json"


@pytest.fixture(scope="module")
def wire() -> dict[str, Any]:
    contract: dict[str, Any] = json.loads(WIRE_PATH.read_text(encoding="utf-8"))
    return contract


def values_of(enum: type[StrEnum]) -> list[str]:
    """Retrieves a StrEnum's values **in declaration order**. The contract's ordering also carries
    meaning (the order states progress).
    """
    return [str(member) for member in enum]


class TestContractItself:
    """Consistency of the contract itself. **If the contract is broken, cross-checking it means
    nothing.**
    """

    def test_the_contract_file_exists(self) -> None:
        assert WIRE_PATH.is_file(), f"{WIRE_PATH} が無い（docs/contracts/wire.md）"

    def test_methods_match_their_namespace(self, wire: dict[str, Any]) -> None:
        """**`stage.*` must never request OS privileges** (docs/architecture/core.md §3).

        If namespaces were mixed up on the contract side, `method_matches_role`'s
        check couldn't be confirmed to "work correctly against a correct contract."
        """
        for role_name, prefix in wire["namespace_by_role"].items():
            for method in wire["methods"][prefix.rstrip(".")]:
                assert method.startswith(prefix), f"{method} が {role_name} の namespace にない"

    def test_no_duplicate_names(self, wire: dict[str, Any]) -> None:
        for group in (*wire["methods"].values(), wire["tauri_commands"], wire["window_labels"]):
            assert len(group) == len(set(group)), f"重複がある: {group}"


class TestCoreMatchesTheContract:
    def test_protocol_version(self, wire: dict[str, Any]) -> None:
        # **All 3 languages must check this.** Missing it in even one lets only that
        # language drift, and Core would just wait for a result and time out, with
        # nothing showing on the Stage.
        assert PROTOCOL_VERSION == wire["protocol_version"]

    def test_namespace_by_role(self, wire: dict[str, Any]) -> None:
        actual = {role.value: prefix for role, prefix in NAMESPACE_BY_ROLE.items()}
        assert actual == wire["namespace_by_role"]
        # Adding a role but forgetting its namespace makes `method_matches_role` raise KeyError.
        assert {role.value for role in Role} == set(wire["namespace_by_role"])

    def test_stage_methods(self, wire: dict[str, Any]) -> None:
        """**Every `stage.*` method Core can send is on the contract, and vice versa.**

        Compared as whole sets rather than name by name. Listing them here again would
        mean a constant could be added to `lumi.transport.methods` and stay unlisted in
        both places at once — which is exactly the drift this file exists to catch.
        """
        assert STAGE_METHODS == set(wire["methods"]["stage"])

    def test_setup_prompt_choices(self, wire: dict[str, Any]) -> None:
        assert {"install": CHOICE_INSTALL, "skip": CHOICE_SKIP} == wire["setup_prompt_choices"]

    def test_inbound_methods(self, wire: dict[str, Any]) -> None:
        """★ **What the Stage may initiate** (ADR-028).

        The real allowlist is `WsServer.on_request`'s registry; this pins the contract
        alongside it. **A method appearing here that Core never registers is unreachable**,
        and one Core registers that is not here is an undocumented route — both are drift
        worth failing on.
        """
        assert list(INBOUND_METHODS) == wire["inbound_methods"]

    def test_inbound_methods_are_stage_namespace(self, wire: dict[str, Any]) -> None:
        """**`stage.*` must never request OS privileges** (docs/architecture/core.md §3)."""
        for method in wire["inbound_methods"]:
            assert method.startswith("stage."), f"{method} は stage.* ではない"

    def test_character_model_reasons(self, wire: dict[str, Any]) -> None:
        """**Core sends a code, and the Stage owns the wording** (ADR-036).

        On the contract because it goes on the wire, and because the Stage looks up
        `character.model.<reason>` by exactly these names. A code added on one side only
        renders as the raw code — **visible, but not what anyone intended.**
        """
        assert list(CHARACTER_MODEL_REASONS) == wire["character_model_reasons"]

    def test_setup_components(self, wire: dict[str, Any]) -> None:
        # **What is being asked about has to be on the contract too.** The panel picks its
        # wording from this value; a drift would ask permission to fetch the wrong thing.
        assert [COMPONENT_TTS, COMPONENT_STT] == wire["setup_components"]

    @pytest.mark.parametrize(
        ("enum", "key"),
        [
            (TtsSetupState, "tts_setup_state"),
            (EngineRuntime, "engine_runtime"),
            (BootPhase, "boot_phase"),
            (LlmSetupState, "llm_setup_state"),
            (SttSetupState, "stt_setup_state"),
            (Source, "settings_source"),
            (Emotion, "emotion"),
            (Viseme, "viseme"),
        ],
    )
    def test_enum_values(self, wire: dict[str, Any], enum: type[StrEnum], key: str) -> None:
        # Adding a value but forgetting it on the Stage side falls back to the Stage's fail-closed
        # default (`?? "starting"` / `?? null`). **Neither an error nor a warning appears.**
        assert values_of(enum) == wire["enums"][key]
