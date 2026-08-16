"""Protocol validation can be tested **without opening a socket**."""

from __future__ import annotations

import json

import pytest

from lumi.transport.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    Role,
    method_matches_role,
    parse_client_message,
    parse_hello,
)


def hello(**overrides: object) -> str:
    message: dict[str, object] = {
        "v": PROTOCOL_VERSION,
        "kind": "hello",
        "role": "shell",
        "token": "t0ken",
    }
    message.update(overrides)
    return json.dumps(message)


class TestParseHello:
    def test_valid(self) -> None:
        parsed = parse_hello(hello())
        assert parsed.role is Role.SHELL
        assert parsed.token == "t0ken"

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "[]",
            hello(kind="command"),
            hello(v=PROTOCOL_VERSION + 1),
            hello(role="core"),
            hello(role=None),
            hello(token=""),
            hello(token=None),
        ],
    )
    def test_rejects(self, raw: str) -> None:
        with pytest.raises(ProtocolError):
            parse_hello(raw)


class TestNamespaceIsolation:
    """`stage.*` must never request OS privileges (docs/architecture/core.md §3)."""

    def test_os_namespace_belongs_to_shell(self) -> None:
        assert method_matches_role("os.window.set_position", Role.SHELL)
        assert not method_matches_role("os.window.set_position", Role.STAGE)

    def test_stage_namespace_belongs_to_stage(self) -> None:
        assert method_matches_role("stage.character.speak", Role.STAGE)
        assert not method_matches_role("stage.character.speak", Role.SHELL)

    def test_unknown_namespace_belongs_to_nobody(self) -> None:
        assert not method_matches_role("ext.tool.invoke", Role.SHELL)
        assert not method_matches_role("ext.tool.invoke", Role.STAGE)


class TestParseClientMessage:
    def test_accepts_result(self) -> None:
        raw = json.dumps({"kind": "result", "corr_id": "abc", "ok": True, "payload": {"x": 1}})
        result = parse_client_message(raw)
        assert result.corr_id == "abc"
        assert result.ok
        assert result.payload == {"x": 1}

    def test_rejects_command_from_client(self) -> None:
        """A client can never issue a `command` to Core. **No such path is ever built.**"""
        raw = json.dumps({"kind": "command", "id": "1", "method": "os.input.click", "payload": {}})
        with pytest.raises(ProtocolError):
            parse_client_message(raw)

    def test_failed_result_must_carry_a_reason(self) -> None:
        raw = json.dumps({"kind": "result", "corr_id": "abc", "ok": False, "payload": {}})
        with pytest.raises(ProtocolError):
            parse_client_message(raw)

    @pytest.mark.parametrize(
        "message",
        [
            {"kind": "result", "ok": True},
            {"kind": "result", "corr_id": "", "ok": True},
            {"kind": "result", "corr_id": "a", "ok": "yes"},
            {"kind": "result", "corr_id": "a", "ok": True, "payload": []},
        ],
    )
    def test_rejects_malformed(self, message: dict[str, object]) -> None:
        with pytest.raises(ProtocolError):
            parse_client_message(json.dumps(message))
