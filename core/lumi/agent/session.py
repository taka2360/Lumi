"""Conversation session — Working Memory plus a **sticky `session_trust`**.

Rules defined in → docs/contracts/provenance.md §Trust of conversation history

## Why `session_trust` lives on Session, not Working Memory

**Working Memory shrinks via `compact()`. `session_trust` must never decrease.**

Putting it on the shrinking side would let taint vanish the instant an untrusted block
falls out of context from budget overflow or compaction. That's exactly the laundering
path Invariant 7 exists to prevent.

## One of the two places `TrustLevel.TRUSTED` may be written

`record_user_utterance()` is **the direct user-input handler**
(voice STT / text input / UI actions all become a Turn by passing through here).

**No other path to escalation exists.** The other is `MemoryStore.confirm()` (Phase 2).
`core/tests/test_kernel_boundaries.py` enumerates and checks the write sites.
"""

from __future__ import annotations

from lumi.provenance import PromptContext, TrustLevel, Turn, join, join_all


class Session:
    """One conversation. **Taint never carries over across sessions** (provenance.md rule 5).

    A new `Session`'s `session_trust` starts at `TRUSTED`.
    This is the user's escape hatch: "cutting context lets you get out of taint."
    """

    __slots__ = ("_dropped_trust", "_session_trust", "_turns")

    def __init__(self) -> None:
        self._turns: list[Turn] = []
        #: sticky. **Once TAINTED, never reverts for this session**
        self._session_trust = TrustLevel.TRUSTED
        #: Join of Turns dropped by `compact()`. **Dropping never lowers history_trust**
        self._dropped_trust = TrustLevel.TRUSTED

    @property
    def turns(self) -> tuple[Turn, ...]:
        return tuple(self._turns)

    @property
    def session_trust(self) -> TrustLevel:
        return self._session_trust

    @property
    def history_trust(self) -> TrustLevel:
        """Join of the Turns currently in Working Memory. **Includes the dropped ones too.**"""
        return join(self._dropped_trust, join_all(turn.trust_level for turn in self._turns))

    def context(self, block_trust: TrustLevel = TrustLevel.TRUSTED) -> PromptContext:
        """This turn's `PromptContext`.

        `block_trust` is **the join of all blocks passed to PromptAssembly**, not the join of
        only the blocks that fit within budget (docs/architecture/agent.md §3).
        """
        return PromptContext(
            block_trust=block_trust,
            history_trust=self.history_trust,
            session_trust=self._session_trust,
        )

    # ── Recording ──────────────────────────────────────────────

    def record_user_utterance(self, text: str) -> Turn:
        """**The direct user-input handler.** The sole point where `TRUSTED` is initially granted.

        **What this does not guarantee**: whether an STT result is really the user's own voice. A
        third party in the same room, or audio played from a speaker, can also get transcribed by
        STT (docs/contracts/provenance.md §Why STT results are TRUSTED, and its limits). Speaker
        identification is out of scope for Phase 1-9, and **is recorded here as a known
        limitation.**
        """
        turn = Turn(role="user", text=text, trust_level=TrustLevel.TRUSTED)
        self._turns.append(turn)
        return turn

    def record_lumi_turn(self, text: str, trust_level: TrustLevel) -> Turn:
        """A Lumi turn. **Pass the join of the inputs used to generate it as `trust_level`.**

        Not "always tainted because it's LLM output" (provenance.md rule 1).
        Treating it as uniformly tainted would taint every turn from the second one onward,
        and the escalation rule would lose its discriminating power.
        """
        turn = Turn(role="lumi", text=text, trust_level=trust_level)
        self._turns.append(turn)
        self.observe(trust_level)
        return turn

    def observe(self, trust_level: TrustLevel) -> None:
        """Something was observed in this session. **Only ever joins. No path lowers it.**"""
        self._session_trust = join(self._session_trust, trust_level)

    # ── Compaction ──────────────────────────────────────────────

    def compact(self, keep_recent: int) -> int:
        """Shrink Working Memory. **The join of dropped Turns is preserved.**

        [In Phase 1, this **just drops them without summarizing**. Summarization requires an
        LLM call, which is Reflection's job (Phase 2). Since **the contract of preserving the
        join stays the same**, `history_trust`'s behavior doesn't change once summarization
        replaces this.]
        """
        if keep_recent < 0:
            raise ValueError("keep_recent must be >= 0")
        if len(self._turns) <= keep_recent:
            return 0

        cut = len(self._turns) - keep_recent
        dropped = self._turns[:cut]
        self._turns = self._turns[cut:]
        self._dropped_trust = join(
            self._dropped_trust, join_all(turn.trust_level for turn in dropped)
        )
        return cut
