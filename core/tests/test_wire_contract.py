"""Core の wire 定数が `docs/contracts/wire.json` と一致する。

契約と規則 → docs/contracts/wire.md / ADR-022

**片方だけ直しても何もエラーにならない**のを、テストの失敗に変えるためのもの。
Lumi の実装は知らない値を安全側に落とす（`?? null` / `unknown_method`）ので、
ずれは例外ではなく**静かな劣化**として出る。ここで落とす。

同じ突き合わせを Stage（`stage/src/core/wire.test.ts`）と
Shell（`shell/src-tauri/src/wire_contract.rs`）も行う。
**1言語でも欠けると、その言語だけが静かにずれる。**
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from lumi.providers.tts.viseme import Viseme
from lumi.setup.coordinator import (
    CHOICE_INSTALL,
    CHOICE_SKIP,
    METHOD_PROMPT,
    METHOD_STATE,
)
from lumi.setup.state import BootPhase, EngineRuntime, TtsSetupState
from lumi.transport.protocol import NAMESPACE_BY_ROLE, PROTOCOL_VERSION, Role

#: リポジトリの `docs/contracts/wire.json`。**実行時には読まない**（配布物に docs は入らない）。
WIRE_PATH = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "wire.json"


@pytest.fixture(scope="module")
def wire() -> dict[str, Any]:
    contract: dict[str, Any] = json.loads(WIRE_PATH.read_text(encoding="utf-8"))
    return contract


def values_of(enum: type[StrEnum]) -> list[str]:
    """StrEnum の値を**宣言順**で取り出す。契約の並びも意味を持つ（状態が進む順）。"""
    return [str(member) for member in enum]


class TestContractItself:
    """契約そのものの整合。**契約が壊れていたら、突き合わせても意味が無い。**"""

    def test_the_contract_file_exists(self) -> None:
        assert WIRE_PATH.is_file(), f"{WIRE_PATH} が無い（docs/contracts/wire.md）"

    def test_methods_match_their_namespace(self, wire: dict[str, Any]) -> None:
        """**`stage.*` は絶対に OS 特権を要求しない**（docs/architecture/core.md §3）。

        契約の側で namespace が混ざっていると、`method_matches_role` の検査が
        「正しい契約に対して正しく働く」ことを確かめられなくなる。
        """
        for role_name, prefix in wire["namespace_by_role"].items():
            for method in wire["methods"][prefix.rstrip(".")]:
                assert method.startswith(prefix), f"{method} が {role_name} の namespace にない"

    def test_no_duplicate_names(self, wire: dict[str, Any]) -> None:
        for group in (*wire["methods"].values(), wire["tauri_commands"], wire["window_labels"]):
            assert len(group) == len(set(group)), f"重複がある: {group}"


class TestCoreMatchesTheContract:
    def test_protocol_version(self, wire: dict[str, Any]) -> None:
        # **3言語すべてが検査すること。** 1つでも抜けるとその言語だけがずれ、
        # Core は result を待って timeout するだけで、Stage には何も出ない。
        assert PROTOCOL_VERSION == wire["protocol_version"]

    def test_namespace_by_role(self, wire: dict[str, Any]) -> None:
        actual = {role.value: prefix for role, prefix in NAMESPACE_BY_ROLE.items()}
        assert actual == wire["namespace_by_role"]
        # role を足して namespace を足し忘れると、`method_matches_role` が KeyError になる。
        assert {role.value for role in Role} == set(wire["namespace_by_role"])

    def test_stage_methods(self, wire: dict[str, Any]) -> None:
        # 発話の method は Phase 1 で `agent/speech.py`（PlaybackScheduler）に移った。
        from lumi.agent.speech import METHOD_SPEECH_ENDED, METHOD_SPEECH_STARTED

        declared = {METHOD_STATE, METHOD_PROMPT, METHOD_SPEECH_STARTED, METHOD_SPEECH_ENDED}
        assert declared == set(wire["methods"]["stage"])

    def test_setup_prompt_choices(self, wire: dict[str, Any]) -> None:
        assert {"install": CHOICE_INSTALL, "skip": CHOICE_SKIP} == wire["setup_prompt_choices"]

    @pytest.mark.parametrize(
        ("enum", "key"),
        [
            (TtsSetupState, "tts_setup_state"),
            (EngineRuntime, "engine_runtime"),
            (BootPhase, "boot_phase"),
            (Viseme, "viseme"),
        ],
    )
    def test_enum_values(self, wire: dict[str, Any], enum: type[StrEnum], key: str) -> None:
        # 値を1つ足して Stage 側に足し忘れると、Stage は fail-closed 側の既定に
        # 落ちる（`?? "starting"` / `?? null`）。**エラーも警告も出ない。**
        assert values_of(enum) == wire["enums"][key]
