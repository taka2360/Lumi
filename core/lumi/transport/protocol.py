"""WS プロトコル — **純粋な型と検証だけ**。ソケットを開かずにテストできる。

確定度: **Provisional**（docs/DESIGN.md §8「WS プロトコルの具体スキーマ」）。

## namespace と経路（docs/architecture/core.md §3）

| namespace | 経路 | 向き |
|---|---|---|
| `os.*`    | Core → Shell | OS 特権操作の依頼 |
| `stage.*` | Core → Stage | Lumi の表現と状態 |

> **`stage.*` は絶対に OS 特権を要求しない。**

これを型で守る。`method` の namespace と接続の role が一致しない送信は、
**送る前に**弾く（`method_matches_role`）。Stage に `os.*` を送れる経路を作らない。

## token は role ごとに分ける

Shell と Stage は**別の token** を持つ。共有すると、乗っ取られた Stage（B2 は Stage を信用しない）が
`role: "shell"` として接続し、**`os.*` の command を横取りできてしまう**。
Shell が両方を生成し、Stage には Stage 用のものだけを渡す。

## Phase 0 で command を出すのは Core だけ

クライアント（Shell / Stage）から Core への `command` は**受け付けない**。
クライアントが送ってよいのは `hello` と、Core の command に対する `result` だけ。

ユーザーの選択（初回セットアップの「取得する / しない」など）は、
**Core が投げた command に対する result** として返る。Core が聞き、ユーザーが答える。
この向きにしておくと、「Stage が Core に何かをさせる」経路が構造的に存在しなくなる（B2）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import uuid4

PROTOCOL_VERSION: Final = 1

#: hello を待つ時間。これを超えたら閉じる（無認証の接続を掴み続けない）。
HELLO_TIMEOUT_S: Final = 5.0

#: WS の ping 間隔と応答待ち。片方が落ちたことを検知する手段。
PING_INTERVAL_S: Final = 5.0
PING_TIMEOUT_S: Final = 10.0


class Role(StrEnum):
    """接続してくるクライアントの種別。"""

    SHELL = "shell"
    STAGE = "stage"


#: role ごとに Core が送ってよい namespace。**ここに行を足すときは B2/B3 を読み直すこと。**
NAMESPACE_BY_ROLE: Final[dict[Role, str]] = {
    Role.SHELL: "os.",
    Role.STAGE: "stage.",
}


class ProtocolError(ValueError):
    """プロトコル違反。**握りつぶさない。** 接続を閉じてログに残す。"""


def method_matches_role(method: str, role: Role) -> bool:
    """`method` の namespace が role に一致するか。"""
    return method.startswith(NAMESPACE_BY_ROLE[role])


def new_id() -> str:
    return uuid4().hex


@dataclass(frozen=True, slots=True)
class Hello:
    """クライアント → Core。接続の最初の1通。"""

    role: Role
    token: str


@dataclass(frozen=True, slots=True)
class Command:
    """Core → クライアント。**結果が要るもの**。"""

    id: str
    method: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "command",
                "id": self.id,
                "method": self.method,
                "payload": self.payload,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class Notify:
    """Core → クライアント。**結果が要らないもの**。

    「結果が要るか？」で command と分ける（docs/contracts/event-model.md の判断基準）。
    進捗の通知や状態の配信はこちら。応答を待たないので、相手が遅くても Core は止まらない。
    """

    method: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "kind": "notify",
                "method": self.method,
                "payload": self.payload,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class Result:
    """クライアント → Core。command への応答。

    `ok=False` のとき `error` に理由が入る。**黙って握りつぶさない。**
    """

    corr_id: str
    ok: bool
    payload: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Welcome:
    """Core → クライアント。認証が通ったことを伝える。"""

    protocol_version: int = PROTOCOL_VERSION

    def to_json(self) -> str:
        return json.dumps({"v": self.protocol_version, "kind": "welcome"}, ensure_ascii=False)


def _require_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"JSON として読めない: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProtocolError("トップレベルがオブジェクトではない")
    return parsed


def parse_hello(raw: str) -> Hello:
    """接続直後の1通目を解釈する。**失敗したら例外**（fail-closed）。"""
    message = _require_object(raw)
    if message.get("kind") != "hello":
        raise ProtocolError("最初のメッセージが hello ではない")
    if message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError(f"プロトコルバージョンが違う: {message.get('v')!r}")

    raw_role = message.get("role")
    if not isinstance(raw_role, str):
        raise ProtocolError("role が無い")
    try:
        role = Role(raw_role)
    except ValueError as exc:
        raise ProtocolError(f"未知の role: {raw_role!r}") from exc

    token = message.get("token")
    if not isinstance(token, str) or not token:
        raise ProtocolError("token が無い")

    return Hello(role=role, token=token)


def parse_client_message(raw: str) -> Result:
    """認証後にクライアントから届いたメッセージを解釈する。

    Phase 0 で受理するのは `result` だけ。**クライアントからの `command` は受理しない**
    （Core が判断の起点であることを、経路の有無で担保する）。
    """
    message = _require_object(raw)
    kind = message.get("kind")
    if kind != "result":
        raise ProtocolError(f"クライアントから受理しない kind: {kind!r}")

    corr_id = message.get("corr_id")
    if not isinstance(corr_id, str) or not corr_id:
        raise ProtocolError("corr_id が無い")

    ok = message.get("ok")
    if not isinstance(ok, bool):
        raise ProtocolError("ok が bool ではない")

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("payload がオブジェクトではない")

    error = message.get("error")
    if error is not None and not isinstance(error, str):
        raise ProtocolError("error が文字列ではない")
    if not ok and not error:
        raise ProtocolError("失敗した result に error が無い")

    return Result(corr_id=corr_id, ok=ok, payload=payload, error=error)
