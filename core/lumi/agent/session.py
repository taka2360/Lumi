"""会話セッション — Working Memory と **sticky な `session_trust`**。

規則の定義 → docs/contracts/provenance.md §会話履歴の trust

## なぜ `session_trust` が Working Memory ではなく Session にあるのか

**Working Memory は `compact()` で減る。`session_trust` は減ってはならない。**

減る側に持たせると、untrusted なブロックが予算超過や圧縮で文脈から落ちた瞬間に
汚染が消える。それは Invariant 7 が防ごうとしたロンダリング経路そのものである。

## `TrustLevel.TRUSTED` を書いてよい2箇所のうちの1つ

`record_user_utterance()` が**ユーザーの直接入力ハンドラ**である
（音声 STT / テキスト入力 / UI 操作は、すべてここを通って Turn になる）。

**ここ以外に昇格の経路を作らない。** もう1箇所は `MemoryStore.confirm()`（Phase 2）。
`core/tests/test_kernel_boundaries.py` が書き込み箇所を列挙して検査している。
"""

from __future__ import annotations

from lumi.provenance import PromptContext, TrustLevel, Turn, join, join_all


class Session:
    """1回の会話。**セッションを跨いで汚染を持ち越さない**（provenance.md 規則5）。

    新しい `Session` の `session_trust` は `TRUSTED` から始まる。
    これが「文脈を切れば汚染から抜けられる」というユーザーの逃げ道になっている。
    """

    __slots__ = ("_dropped_trust", "_session_trust", "_turns")

    def __init__(self) -> None:
        self._turns: list[Turn] = []
        #: sticky。**一度 TAINTED になったらこのセッションでは戻らない**
        self._session_trust = TrustLevel.TRUSTED
        #: `compact()` で落とした Turn の join。**落としても history_trust を下げない**
        self._dropped_trust = TrustLevel.TRUSTED

    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    @property
    def session_trust(self) -> TrustLevel:
        return self._session_trust

    @property
    def history_trust(self) -> TrustLevel:
        """Working Memory に載っている Turn の join。**落とした分も含める。**"""
        return join(self._dropped_trust, join_all(turn.trust_level for turn in self._turns))

    def context(self, block_trust: TrustLevel = TrustLevel.TRUSTED) -> PromptContext:
        """このターンの `PromptContext`。

        `block_trust` は **PromptAssembly に渡した全ブロックの join**であって、
        予算内に載ったブロックの join ではない（docs/architecture/agent.md §3）。
        """
        return PromptContext(
            block_trust=block_trust,
            history_trust=self.history_trust,
            session_trust=self._session_trust,
        )

    # ── 記録 ──────────────────────────────────────────────

    def record_user_utterance(self, text: str) -> Turn:
        """**ユーザーの直接入力ハンドラ。** ここが `TRUSTED` の唯一の初期付与点。

        **保証しないこと**: STT の結果が本当にユーザー本人の声かは分からない。
        同じ部屋の第三者や、スピーカーから流れた音声も STT されうる
        （docs/contracts/provenance.md §STT 結果を TRUSTED にする理由と、その限界）。
        話者識別は Phase 1〜9 のスコープ外であり、**既知の限界として記録している。**
        """
        turn = Turn(role="user", text=text, trust_level=TrustLevel.TRUSTED)
        self._turns.append(turn)
        return turn

    def record_lumi_turn(self, text: str, trust_level: TrustLevel) -> Turn:
        """Lumi のターン。**`trust_level` は生成に使った入力の join を渡すこと。**

        「LLM 出力だから常に tainted」ではない（provenance.md 規則1）。
        一律 tainted にすると2ターン目以降が常に汚染され、昇格規則が判別力を失う。
        """
        turn = Turn(role="lumi", text=text, trust_level=trust_level)
        self._turns.append(turn)
        self.observe(trust_level)
        return turn

    def observe(self, trust_level: TrustLevel) -> None:
        """このセッションで何かを見た。**join するだけ。下げる経路は無い。**"""
        self._session_trust = join(self._session_trust, trust_level)

    # ── 圧縮 ──────────────────────────────────────────────

    def compact(self, keep_recent: int) -> int:
        """Working Memory を縮める。**落とした Turn の join は残る。**

        〔Phase 1 では**要約せずに落とすだけ**。要約には LLM 呼び出しが要り、
        それは Reflection（Phase 2）の仕事である。**join を保存するという契約は同じ**なので、
        要約に置き換わっても `history_trust` の振る舞いは変わらない。〕
        """
        if keep_recent < 0:
            raise ValueError("keep_recent は 0 以上")
        if len(self._turns) <= keep_recent:
            return 0

        cut = len(self._turns) - keep_recent
        dropped = self._turns[:cut]
        self._turns = self._turns[cut:]
        self._dropped_trust = join(
            self._dropped_trust, join_all(turn.trust_level for turn in dropped)
        )
        return cut
