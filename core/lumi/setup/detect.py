"""既存インストールの検出。**外部通信しない。**

見るのはローカルのファイルシステムと 127.0.0.1 だけ
（docs/architecture/setup.md §6）。
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lumi import logging as lumi_logging
from lumi import paths
from lumi.setup.engines import AIVISSPEECH_ENGINE, EngineArtifact

log = lumi_logging.get_logger(__name__)

#: ポートが開いているかを見るときの待ち時間。**長く待たない**（起動を遅らせない）。
PROBE_TIMEOUT_S = 0.3


@dataclass(frozen=True, slots=True)
class KnownEngine:
    """検出対象のエンジン。"""

    name: str
    display_name: str
    port: int
    #: `%LOCALAPPDATA%` などからの相対ではなく、環境変数名と続きのパスで持つ。
    candidates: tuple[tuple[str, str], ...]


#: 検出対象。VOICEVOX は**同梱しないが、ユーザーが入れたものは使う**（ADR-019）。
KNOWN_ENGINES: tuple[KnownEngine, ...] = (
    KnownEngine(
        name="aivisspeech",
        display_name="AivisSpeech",
        port=AIVISSPEECH_ENGINE.default_port,
        candidates=(
            ("LOCALAPPDATA", r"Programs\AivisSpeech\AivisSpeech-Engine\run.exe"),
            ("ProgramFiles", r"AivisSpeech\AivisSpeech-Engine\run.exe"),
        ),
    ),
    KnownEngine(
        name="voicevox",
        display_name="VOICEVOX",
        port=50021,
        candidates=(
            ("LOCALAPPDATA", r"Programs\VOICEVOX\vv-engine\run.exe"),
            ("ProgramFiles", r"VOICEVOX\vv-engine\run.exe"),
        ),
    ),
)


#: Ollama。**Lumi は取得もインストールもしない。検出だけ**（ADR-023）。
#: TTS エンジンと違い `KNOWN_ENGINES` には入れない — 取得の対象ではないため、
#: 同じ列挙に混ぜると「取得できるもの」として扱う経路が生まれる。
OLLAMA: KnownEngine = KnownEngine(
    name="ollama",
    display_name="Ollama",
    port=11434,
    candidates=(
        ("LOCALAPPDATA", r"Programs\Ollama\ollama.exe"),
        ("ProgramFiles", r"Ollama\ollama.exe"),
    ),
)


@dataclass(frozen=True, slots=True)
class DetectedEngine:
    name: str
    display_name: str
    port: int
    executable: Path | None
    #: すでに起動しているか。起動していれば**二重に起動しない**。
    running: bool
    #: Lumi が入れたものか。**ユーザー自身が入れたものと区別する**
    #: （状態が `installed` か `detected` かはここで決まる → setup.md §2）。
    managed_by_lumi: bool = False
    #: Lumi が入れた場合のバージョン。
    version: str | None = None


def candidate_executables(engine: KnownEngine, env: Mapping[str, str]) -> list[Path]:
    """パス候補を組み立てる。**純粋関数**（環境変数を引数で受け取る）。"""
    results: list[Path] = []
    for variable, suffix in engine.candidates:
        base = env.get(variable)
        if base:
            results.append(Path(base) / suffix)
    return results


def find_installed_by_lumi(artifact: EngineArtifact, root: Path) -> Path | None:
    """Lumi が入れたエンジンの実行体を探す。"""
    install_dir = root / f"{artifact.name}-{artifact.version}"
    if not install_dir.is_dir():
        return None
    return next(install_dir.rglob(artifact.executable_name), None)


async def is_port_open(port: int, *, host: str = "127.0.0.1") -> bool:
    """**127.0.0.1 のみ。** 外に向けて開かない。"""
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            _reader, writer = await asyncio.open_connection(host, port)
    except (OSError, TimeoutError):
        return False

    writer.close()
    # **`wait_closed()` を無制限に待たない。** 相手の実装次第で戻ってこないことがあり、
    # 「ポートが開いているか」を知るのに待つ必要は無い（起動を遅らせない）。
    with contextlib.suppress(OSError, TimeoutError, asyncio.CancelledError):
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            await writer.wait_closed()
    return True


async def detect_engines(env: Mapping[str, str]) -> list[DetectedEngine]:
    """使えるエンジンを列挙する。**外部通信しない。**"""
    found: list[DetectedEngine] = []

    lumi_executable = find_installed_by_lumi(AIVISSPEECH_ENGINE, paths.engines_dir())

    for engine in KNOWN_ENGINES:
        executable = next(
            (path for path in candidate_executables(engine, env) if path.is_file()), None
        )
        managed_by_lumi = False
        if executable is None and engine.name == AIVISSPEECH_ENGINE.name:
            executable = lumi_executable
            managed_by_lumi = executable is not None
        running = await is_port_open(engine.port)
        if executable is None and not running:
            continue
        found.append(
            DetectedEngine(
                name=engine.name,
                display_name=engine.display_name,
                port=engine.port,
                executable=executable,
                running=running,
                managed_by_lumi=managed_by_lumi,
                version=AIVISSPEECH_ENGINE.version if managed_by_lumi else None,
            )
        )
        log.info(
            "setup.engine.detected",
            engine=engine.name,
            executable=str(executable) if executable else None,
            running=running,
        )

    return found


def find_on_path(command: str, env: Mapping[str, str]) -> Path | None:
    """`PATH` から実行体を探す。**環境変数を引数で受け取る**（純粋関数として試験できる）。

    ユーザーが自分で入れる前提のもの（Ollama）は、決まった場所ではなく
    `PATH` に居ることが多い。決め打ちのパスだけを見ると「入っているのに無い」と言うことになる。
    """
    found = shutil.which(command, path=env.get("PATH"))
    return Path(found) if found else None


async def detect_ollama(env: Mapping[str, str]) -> DetectedEngine | None:
    """Ollama を探す。**外部通信しない**（ローカルのパスと 127.0.0.1 だけ）。

    見つからなければ `None`。これが setup.md §2b の `not_configured` になる。
    **「入っていない」と「起動していない」を、ここで区別する**
    （Provider は HTTP しか見えないので区別できない）。
    """
    executable = next(
        (path for path in candidate_executables(OLLAMA, env) if path.is_file()), None
    ) or find_on_path("ollama", env)
    running = await is_port_open(OLLAMA.port)
    if executable is None and not running:
        return None
    return DetectedEngine(
        name=OLLAMA.name,
        display_name=OLLAMA.display_name,
        port=OLLAMA.port,
        executable=executable,
        running=running,
    )
