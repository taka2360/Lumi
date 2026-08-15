"""初回セットアップの進行役。**判断はここに集める**（Stage は表示するだけ）。

設計 → docs/architecture/setup.md

流れ:

```
起動 → 検出（ローカルのみ）→ 状態が決まる
Stage が接続 → 状態を配信
  状態が not_configured で、まだ聞いていなければ → 取得するかを尋ねる
    「取得する」→ installing（進捗を配信）→ installed / failed
    「取得しない」→ not_configured のまま。**聞いたことだけ覚える**
```
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from lumi import logging as lumi_logging
from lumi import paths
from lumi.setup.detect import detect_engines
from lumi.setup.engines import AIVISSPEECH_ENGINE
from lumi.setup.install import SetupError, install_engine
from lumi.setup.state import EngineRuntime, SetupAnswers, TtsSetup, TtsSetupState
from lumi.transport.protocol import Role
from lumi.transport.server import NotConnectedError, WsServer

log = lumi_logging.get_logger(__name__)

#: ユーザーが選ぶのを待つ時間。**人間の時間**なので長い。
PROMPT_TIMEOUT_S = 600.0

#: 尋ねる回数の上限（失敗後の聞き直しを含む）。**しつこくしない。**
MAX_PROMPTS = 2

#: 状態を配信する method（Core → Stage）。
METHOD_STATE = "stage.setup.state"
#: 取得するかを尋ねる method（Core → Stage、結果を待つ）。
METHOD_PROMPT = "stage.setup.prompt"

#: Stage が result で返してくる選択肢。**線上に出る値**なので docs/contracts/wire.json が正
#: （→ ADR-022）。`CHOICE_SKIP` は比較に使わないが、**契約の片側だけを書かない**ために置く。
CHOICE_INSTALL = "install"
CHOICE_SKIP = "skip"


class SetupCoordinator:
    def __init__(self, server: WsServer, env: Mapping[str, str]) -> None:
        self._server = server
        self._env = env
        self._state = TtsSetup(state=TtsSetupState.UNKNOWN)
        self._answers_path: Path = paths.setup_state_file()
        self._answers = SetupAnswers()
        # **2つを分ける。** `_prompting` は「この一連の処理に入っているか」（多重起動の防止）、
        # `_awaiting_answer` は「いま画面に質問が出ているか」（起動フェーズ）。
        # 混ぜると、取得が終わった直後に質問画面が一瞬戻る（テストで踏んだ）。
        self._prompting = False
        self._awaiting_answer = False
        # **Stage は検出より先に繋がりうる。** 実測で先に繋がった（2026-08-15）。
        # 状態が unknown のまま prompt の判定をすると、聞くべきときに聞かなくなる。
        self._initialized = asyncio.Event()

    @property
    def state(self) -> TtsSetup:
        return self._state

    async def initialize(self) -> None:
        """起動時に一度だけ。**外部通信しない。**"""
        try:
            self._answers = await asyncio.to_thread(SetupAnswers.load, self._answers_path)
            await self._redetect()
        finally:
            # 失敗しても待っている側を放置しない。
            self._initialized.set()

    async def _redetect(self) -> None:
        engines = await detect_engines(self._env)
        usable = next((engine for engine in engines if engine.executable or engine.running), None)
        if usable is None:
            await self._set_state(TtsSetup(state=TtsSetupState.NOT_CONFIGURED))
            return
        # Lumi が入れたものと、ユーザー自身が入れたものを区別する（setup.md §2）。
        await self._set_state(
            TtsSetup(
                state=(
                    TtsSetupState.INSTALLED if usable.managed_by_lumi else TtsSetupState.DETECTED
                ),
                engine_name=usable.display_name,
                version=usable.version,
                port=usable.port,
                executable=str(usable.executable) if usable.executable else None,
            )
        )

    async def _set_state(self, state: TtsSetup) -> None:
        self._state = state
        await self._broadcast()

    async def _broadcast(self) -> None:
        """今の状態を配る。**起動フェーズを含む**（docs/architecture/ui.md）。

        フェーズは「尋ねている最中か」にも依るので、状態が変わらなくても
        **尋ね始めた / 尋ね終わったときには配り直す**。
        """
        payload = self._state.to_payload(prompting=self._awaiting_answer)
        log.info("setup.state", **payload)
        await self._server.notify(Role.STAGE, METHOD_STATE, payload)

    async def set_runtime(self, runtime: EngineRuntime) -> None:
        """エンジン**プロセス**の状態が変わった。

        導入の状態とは別の軸だが、Stage に配るのは同じ1つの状態なので、
        **配信の出口をここに一本化する**（2箇所から送ると順序が保証できない）。
        """
        await self._set_state(replace(self._state, runtime=runtime))

    async def on_stage_connected(self) -> None:
        """Stage が繋がった。現在の状態を配り、必要なら尋ねる。

        **検出が終わるのを待つ。** Stage の接続の方が先に来ることがあり、
        待たないと状態が `unknown` のまま「尋ねなくてよい」と判断してしまう。
        """
        await self._initialized.wait()
        await self._broadcast()

        if self._state.state is not TtsSetupState.NOT_CONFIGURED:
            return
        if self._answers.tts_prompt_answered or self._prompting:
            # **一度答えたら二度と聞かない。** 起動のたびに聞くのは鬱陶しさの典型。
            return

        self._prompting = True
        try:
            await self._ask_and_maybe_install()
        finally:
            self._prompting = False
            self._awaiting_answer = False
            # 答え終わった（あるいは諦めた）ことを配る。**待たせっぱなしにしない。**
            await self._broadcast()

    async def _ask_and_maybe_install(self) -> None:
        """尋ねて、選ばれたら取得する。**失敗したら一度だけ聞き直す。**

        聞き直すのは1回まで。それ以上は鬱陶しさの方が害になる。
        「まだ試していない」に戻さないので、状態は `failed` のまま残る。
        """
        retry = False
        reason: str | None = None

        for _ in range(MAX_PROMPTS):
            # **質問を出す前にフェーズを配る。** これを忘れると Stage は
            # ローディングを出したまま、その裏で質問を出すことになる。
            self._awaiting_answer = True
            await self._broadcast()
            try:
                result = await self._server.invoke(
                    Role.STAGE,
                    METHOD_PROMPT,
                    {"retry": retry, "reason": reason},
                    timeout=PROMPT_TIMEOUT_S,
                )
            except (NotConnectedError, TimeoutError):
                # 答えをもらえなかった。**「聞いた」ことにしない**（次の起動でまた尋ねる）。
                log.info("setup.prompt.unanswered")
                return

            # **答えが来た時点で質問は消える。** 取得中のフェーズを質問で塗り潰さない。
            self._awaiting_answer = False
            choice = result.payload.get("choice") if result.ok else None
            # **未知の答えは「取得しない」と同じ扱いにする**（fail-closed）。
            install = choice == CHOICE_INSTALL
            log.info("setup.prompt.answered", choice=choice, install=install)

            self._answers = SetupAnswers(tts_prompt_answered=True)
            await asyncio.to_thread(self._answers.save, self._answers_path)

            if not install:
                return

            await self.install_tts_engine()
            if self._state.state is not TtsSetupState.FAILED:
                return

            retry = True
            reason = self._state.reason

    async def install_tts_engine(self) -> None:
        """ユーザーが選んだので取得する。**ここより前に外部通信は起きない。**"""
        artifact = AIVISSPEECH_ENGINE
        await self._set_state(
            TtsSetup(
                state=TtsSetupState.INSTALLING,
                engine_name=artifact.display_name,
                version=artifact.version,
                progress=0.0,
            )
        )

        last_sent = -1.0

        async def report(fraction: float) -> None:
            nonlocal last_sent
            # 1% ごとに配る。毎チャンク送ると WS が進捗で埋まる。
            if fraction - last_sent < 0.01 and fraction < 1.0:
                return
            last_sent = fraction
            # 進捗だけを更新する。**`_state` は INSTALLING のまま**なので
            # フェーズも installing のままになる。
            #
            # **配信は必ず `_set_state` を通す。** ここで `notify` を直に呼ぶと、
            # `to_payload(prompting=...)` に渡す旗を自分で選ぶことになり、
            # `_prompting`（この処理に入っているか）と `_awaiting_answer`
            # （いま画面に質問が出ているか）を取り違える。取り違えると、
            # 取得中の 200MB の間ずっと `boot=setup` が流れ、進捗が出ない。
            await self._set_state(replace(self._state, progress=fraction))

        try:
            executable = await install_engine(artifact, paths.engines_dir(), progress=report)
        except SetupError as error:
            log.warning("setup.install.failed", reason=error.reason, detail=error.detail)
            await self._set_state(
                TtsSetup(
                    state=TtsSetupState.FAILED,
                    engine_name=artifact.display_name,
                    version=artifact.version,
                    reason=error.reason,
                )
            )
            return
        except asyncio.CancelledError:
            await self._set_state(TtsSetup(state=TtsSetupState.FAILED, reason="cancelled"))
            raise
        except Exception as error:
            # 想定外でも**黙って未設定に戻さない**。何が起きたかを残す。
            log.exception("setup.install.crashed")
            await self._set_state(TtsSetup(state=TtsSetupState.FAILED, reason="unexpected_error"))
            del error
            return

        await self._set_state(
            TtsSetup(
                state=TtsSetupState.INSTALLED,
                engine_name=artifact.display_name,
                version=artifact.version,
                port=artifact.default_port,
                executable=str(executable),
            )
        )
