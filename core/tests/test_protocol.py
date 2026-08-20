"""Protocol validation can be tested **without opening a socket**."""

from __future__ import annotations

import json

import pytest

from lumi.transport.protocol import (
    PROTOCOL_VERSION,
    Command,
    Notify,
    ProtocolError,
    Request,
    Result,
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
    def test_core_generated_frames_all_carry_the_protocol_version(self) -> None:
        frames = (
            Command(id="c1", method="stage.character.speak", payload={}).to_json(),
            Notify(method="stage.setup.state", payload={}).to_json(),
            Result(corr_id="r1", ok=False, payload={}, error="internal_error").encode(),
        )

        assert [json.loads(frame)["v"] for frame in frames] == [PROTOCOL_VERSION] * 3

    def test_accepts_result(self) -> None:
        raw = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "result",
                "corr_id": "abc",
                "ok": True,
                "payload": {"x": 1},
            }
        )
        result = parse_client_message(raw)
        assert isinstance(result, Result)
        assert result.corr_id == "abc"
        assert result.ok
        assert result.payload == {"x": 1}

    def test_rejects_command_from_client(self) -> None:
        """★ **A client still never issues a `command`.**

        A `request` was added (ADR-028), and the distinction is the point: Core → Stage is
        a command (Core decided), Stage → Core is a request (Core decides). **Accepting a
        `command` here would erase that asymmetry** and with it Invariant 1's guarantee.
        """
        raw = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "command",
                "id": "1",
                "method": "os.input.click",
                "payload": {},
            }
        )
        with pytest.raises(ProtocolError):
            parse_client_message(raw)

    def test_accepts_request(self) -> None:
        raw = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "request",
                "id": "r1",
                "method": "stage.settings.update",
                "payload": {"a": 1},
            }
        )
        request = parse_client_message(raw)
        assert isinstance(request, Request)
        assert request.method == "stage.settings.update"
        assert request.payload == {"a": 1}

    def test_a_request_without_an_id_is_refused(self) -> None:
        """**Core must always be able to answer.** A request nobody can reply to leaves the
        client waiting forever.
        """
        raw = json.dumps(
            {"v": PROTOCOL_VERSION, "kind": "request", "method": "stage.settings.update"}
        )
        with pytest.raises(ProtocolError):
            parse_client_message(raw)

    def test_a_request_without_a_method_is_refused(self) -> None:
        raw = json.dumps({"v": PROTOCOL_VERSION, "kind": "request", "id": "r1"})
        with pytest.raises(ProtocolError):
            parse_client_message(raw)

    def test_the_parser_never_decides_whether_a_method_is_allowed(self) -> None:
        """**The allowlist is the server's registry** (ADR-028), in one place.

        Parsing an unregistered method is fine; *serving* it is what gets refused.
        Splitting the check across both would make "is this reachable" a two-file question.
        """
        raw = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "request",
                "id": "r1",
                "method": "stage.anything",
                "payload": {},
            }
        )
        assert isinstance(parse_client_message(raw), Request)

    def test_failed_result_must_carry_a_reason(self) -> None:
        raw = json.dumps(
            {"v": PROTOCOL_VERSION, "kind": "result", "corr_id": "abc", "ok": False, "payload": {}}
        )
        with pytest.raises(ProtocolError):
            parse_client_message(raw)

    @pytest.mark.parametrize(
        "message",
        [
            {"v": PROTOCOL_VERSION, "kind": "result", "ok": True},
            {"v": PROTOCOL_VERSION, "kind": "result", "corr_id": "", "ok": True},
            {"v": PROTOCOL_VERSION, "kind": "result", "corr_id": "a", "ok": "yes"},
            {"v": PROTOCOL_VERSION, "kind": "result", "corr_id": "a", "ok": True, "payload": []},
            # **No version at all is a version mismatch**, not a lenient case
            {"kind": "result", "corr_id": "a", "ok": True},
            {"v": PROTOCOL_VERSION + 1, "kind": "result", "corr_id": "a", "ok": True},
        ],
    )
    def test_rejects_malformed(self, message: dict[str, object]) -> None:
        with pytest.raises(ProtocolError):
            parse_client_message(json.dumps(message))
