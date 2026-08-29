"""Putting remembered things in front of the LLM.

Design → docs/architecture/memory.md §3 ("プロンプトへの反映") / Isolation format →
docs/contracts/provenance.md

## How a memory is presented depends on how Lumi came to hold it

| assertion_mode | presented as |
|---|---|
| `user_confirmed` / `user_stated` | a fact |
| `inferred` | something Lumi worked out from conversation |
| `self_generated` | Lumi's own guess |
| `external` | from outside, and isolated |

**This is character design as much as security.** The difference between an AI that can
say "though I might just be imagining it" and one that states its hallucinations as fact is
decided here — by whether the prompt still knows where each line came from.

## Tainted memories go in the isolation block

A memory whose `trust_level` is `TAINTED` descends from something outside Lumi's world, and
**summarizing it into a belief did not clean it** (Invariant 7). It is grouped separately so
`PromptAssembly` puts it behind the isolation header, and Policy's forced escalation
remains the last line of defence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Final

from lumi.agent.prompt import ContextBlock
from lumi.memory.records import AssertionMode, MemoryRecord
from lumi.provenance import ProvenanceClass, TrustLevel, join_all, provenance_from

#: Header for the memories Lumi holds on its own authority.
RECALL_HEADER: Final = "What you remember about this person:"

#: How each kind of grounds is worded. **English, like the rest of the frame** — the memory
#: itself keeps whatever language it was said in.
PRESENTATION: Final[Mapping[AssertionMode, str]] = {
    AssertionMode.USER_CONFIRMED: "{content}",
    AssertionMode.USER_STATED: "{content}",
    AssertionMode.INFERRED: "{content} (you inferred this from conversation; not stated)",
    AssertionMode.SELF_GENERATED: "{content} (your own guess, nothing confirmed it)",
    AssertionMode.EXTERNAL: "{content} (from an external source)",
}


#: What the frame around one block of memories costs: the header, and the
#: `--- source ---` wrapper `ContextBlock.render` adds. **Reserved rather than measured per
#: turn** — it does not depend on which memories were chosen, and the budget has to be
#: known before they are.
BLOCK_OVERHEAD_TOKENS: Final = 40

#: How many blocks `to_blocks` can produce: **one for what Lumi may state, one for what it
#: may not.** Both frames are reserved because which of them will exist is only known after
#: the search has already been budgeted.
MAX_MEMORY_BLOCKS: Final = 2


def cost_of(record: MemoryRecord, estimate: Callable[[str], int]) -> int:
    """What one memory costs the prompt. **The rendered line, not the raw content.**

    Budgeting on `content` alone under-counts by the qualifier that `present` adds — and
    the qualifiers are longest on exactly the memories that are least certain, so the
    overflow lands on the ones Lumi should be most careful about.
    """
    return estimate(present(record))


def present(record: MemoryRecord) -> str:
    """One memory as a line of prompt. **Pure.**

    The qualifier travels with the content, rather than being stated once for a group:
    a list where only the header says "some of these are guesses" is a list where the LLM
    picks whichever it likes and presents it as fact.
    """
    return "- " + PRESENTATION[record.assertion_mode].format(content=record.content)


def to_blocks(records: Sequence[MemoryRecord]) -> tuple[ContextBlock, ...]:
    """Group memories into at most two blocks: **what Lumi may state, and what it may not.**

    Grouped rather than one block per memory, because a `--- source ---` wrapper around
    every line costs budget that memories should be spending on content.
    """
    trusted = [record for record in records if record.trust_level is TrustLevel.TRUSTED]
    tainted = [record for record in records if record.trust_level is TrustLevel.TAINTED]

    blocks: list[ContextBlock] = []
    if trusted:
        blocks.append(
            ContextBlock(
                source="memory",
                content=RECALL_HEADER + "\n" + "\n".join(present(r) for r in trusted),
                provenance_class=ProvenanceClass.TRUSTED,
                # **Joined from the records, not asserted here.** Writing the literal would
                # make this a place that grants trust; the join can only preserve or lower
                # it, and the grouping above is what decided which records are in the block.
                trust_level=join_all(record.trust_level for record in trusted),
            )
        )
    if tainted:
        blocks.append(
            ContextBlock(
                source="memory",
                content=RECALL_HEADER + "\n" + "\n".join(present(r) for r in tainted),
                # **`UNTRUSTED` only if a record actually says so.** Everything else that
                # is tainted got there by derivation, and the label is what the user is
                # shown when they ask where a belief came from.
                provenance_class=provenance_from(
                    TrustLevel.TAINTED, (r.provenance_class for r in tainted)
                ),
                trust_level=TrustLevel.TAINTED,
                detail="derived from external content",
            )
        )
    return tuple(blocks)
