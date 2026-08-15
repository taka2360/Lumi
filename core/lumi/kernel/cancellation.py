"""Cancellation 契約 — 3種。

契約の唯一の定義場所 → docs/contracts/state-machines.md

> **`cancel_token.fire()` で全部止まるという仮定は誤りである。**

subprocess・HTTP リクエスト・GPU 推論・OS 入力インジェクションは、
キャンセル方式も、そもそも中断可能かも異なる。**止め方を宣言させる。**
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum


class Cancellation(StrEnum):
    """**すべての Tool / 仕事はこの3つのうち1つを宣言しなければならない。**"""

    #: cancel_token を定期的に見て、次のチェックポイントで安全に中断する。
    #: 例: LLM ストリーム、ループ処理、ファイル走査
    COOPERATIVE = "cooperative"
    #: 外部から強制終了できる。例: subprocess、HTTP、**TTS 再生の停止**
    HARD = "hard"
    #: 開始したら完了まで止められない。例: 単一の GPU 推論、OS 入力の1イベント
    #: **副作用を持つものは risk >= L3 に固定される**（登録時に検証。permission.md §3.1）
    NON_CANCELLABLE = "non_cancellable"


class CancelToken:
    """「止めてくれ」を伝えるだけのもの。**止まったことは保証しない。**

    保証するのは「fire されたことが観測できる」ことだけであり、
    実際に止まるかは受け手の契約（`Cancellation`）による。
    ここを混同すると `non_cancellable` の扱いを間違える。
    """

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    def fire(self, reason: str) -> None:
        """**冪等。** 2回目以降は無視し、**最初の理由を残す。**

        後から上書きすると「なぜ止まったか」が最後の1つに塗り潰される。
        barge-in の調査で効くのは最初の理由の方である。
        """
        if self._event.is_set():
            return
        self._reason = reason
        self._event.set()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait(self) -> str:
        """fire されるまで待ち、理由を返す。"""
        await self._event.wait()
        return self._reason or ""


class CancellationContractError(ValueError):
    """契約と実装が食い違っている。**起動時か登録時に落とす**（fail-closed）。"""


@dataclass(frozen=True, slots=True)
class Cancellable:
    """Activity にぶら下がる「止められる仕事」。

    Tool 実行・LLM ストリーム・TTS 生成のいずれもこれとして Arbiter に見える。
    **kernel は Tool を知らない**（依存の向き。docs/architecture/core.md §4）ので、
    知っているのは「止め方の契約」だけである。
    """

    id: str
    #: Inspector と `InterruptResult` に出る名前。人間が読む
    label: str
    contract: Cancellation
    #: `cooperative` の停止手段
    cancel_token: CancelToken = field(default_factory=CancelToken)
    #: `hard` の停止手段。**`hard` なら必須**
    kill: Callable[[], Awaitable[None]] | None = None
    #: 仕事の側が終了時（正常・中断のどちらでも）に立てる。
    #: **これが無いと「止まったか」を Arbiter が知る手段が無く、猶予時間が意味を持たない。**
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if self.contract is Cancellation.HARD and self.kill is None:
            # 宣言だけ hard で止める手段が無いのは、barge-in が効かないのと同じ。
            raise CancellationContractError(f"{self.label}: hard を宣言したが kill が無い")

    def mark_finished(self) -> None:
        """仕事の側が `finally` で呼ぶ。**正常終了でも呼ぶ**（区別は Arbiter がしない）。"""
        self.finished.set()
