"""TTS セットアップ状態。状態機械の定義は docs/architecture/setup.md §2。

**「取得しなかった」と「試して失敗した」を混ぜない。** ユーザーに要求する行動が違う。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class TtsSetupState(StrEnum):
    #: まだ調べていない。
    UNKNOWN = "unknown"
    #: 使えるエンジンが無く、ユーザーはまだ取得を選んでいない。
    NOT_CONFIGURED = "not_configured"
    #: ユーザーが別途インストールしたエンジンを見つけた。
    DETECTED = "detected"
    #: 取得中。
    INSTALLING = "installing"
    #: Lumi が入れたエンジンが使える。
    INSTALLED = "installed"
    #: 取得を試みて失敗した。**未設定に戻さない。**
    FAILED = "failed"


class EngineRuntime(StrEnum):
    """エンジン**プロセス**の状態。導入の状態（`TtsSetupState`）とは別の軸。

    docs/architecture/setup.md「導入の状態と、プロセスの状態を混ぜない」

    1つの enum に混ぜると「入っているのに起動できない」を表現できず、
    ユーザーに「取得してください」と嘘の案内をすることになる。
    """

    #: 起動していない。
    STOPPED = "stopped"
    #: 起動中。まだ応答しない（初回はエンジン自身のモデル取得で数分かかる）。
    STARTING = "starting"
    #: 喋れる。
    READY = "ready"
    #: 入っているのに起動できない = **壊れている**。
    FAILED = "failed"


class BootPhase(StrEnum):
    """起動のどこまで進んだか。**キャラクターを出してよいかを Core が決める。**

    定義 → docs/architecture/ui.md「起動フェーズ」

    立っているのに反応しないキャラクターは**壊れて見える**。エンジンの取得に数分、
    起動に十数秒かかる間は、代わりに何が起きているかを出す。
    """

    #: ユーザーの選択を待っている。
    SETUP = "setup"
    #: エンジンを取得中。
    INSTALLING = "installing"
    #: エンジンのプロセスを起動中。
    STARTING = "starting"
    #: **キャラクターを出してよい。**
    READY = "ready"


@dataclass(frozen=True, slots=True)
class TtsSetup:
    """Stage に配る状態。Stage は**表示するだけ**。"""

    state: TtsSetupState
    engine_name: str | None = None
    version: str | None = None
    port: int | None = None
    executable: str | None = None
    #: 失敗の理由。`FAILED` のときだけ入る。**黙って劣化させないための文言**。
    reason: str | None = None
    #: 取得の進捗（0.0-1.0）。`INSTALLING` のときだけ入る。
    progress: float | None = None
    #: エンジン**プロセス**の状態。導入の状態とは独立に動く。
    runtime: EngineRuntime = EngineRuntime.STOPPED

    def to_payload(self, *, prompting: bool = False) -> dict[str, Any]:
        return {
            "boot": str(boot_phase(self, prompting=prompting)),
            "state": str(self.state),
            "engine_name": self.engine_name,
            "version": self.version,
            "port": self.port,
            "executable": self.executable,
            "reason": self.reason,
            "progress": self.progress,
            "runtime": str(self.runtime),
        }

    @property
    def usable(self) -> bool:
        """このまま喋れるか。"""
        return self.state in (TtsSetupState.DETECTED, TtsSetupState.INSTALLED)


def boot_phase(setup: TtsSetup, *, prompting: bool) -> BootPhase:
    """起動フェーズを決める。**純粋関数**（docs/architecture/ui.md）。

    **待たせてよいのは「これから使えるようになる」ときだけ。**
    取得しない選択も、取得の失敗も `READY` にする。喋れないことと、
    Lumi が起動していないことは別であり、**キャラクターを人質にしない**。
    """
    if prompting:
        return BootPhase.SETUP
    if setup.state is TtsSetupState.INSTALLING:
        return BootPhase.INSTALLING
    if setup.usable and setup.runtime in (EngineRuntime.STOPPED, EngineRuntime.STARTING):
        # **まだ起動していないだけで、これから起動する。**
        # ここを READY にすると、キャラクターが一瞬出てから引っ込む（実測で踏んだ）。
        return BootPhase.STARTING
    return BootPhase.READY


@dataclass(frozen=True, slots=True)
class SetupAnswers:
    """初回セットアップで**もう聞いたこと**。

    汎用の設定ストアにしない（設定の保存形式は roadmap 未確定事項 #9 / Phase 1）。
    ここが持つのは「TTS の取得を尋ねて答えをもらったか」だけ。
    """

    tts_prompt_answered: bool = False

    @classmethod
    def load(cls, path: Path) -> SetupAnswers:
        """読めなければ「まだ聞いていない」として扱う（壊れたファイルで起動を止めない）。"""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(tts_prompt_answered=bool(raw.get("tts_prompt_answered", False)))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tts_prompt_answered": self.tts_prompt_answered}, ensure_ascii=False),
            encoding="utf-8",
        )
