"""Memory — what Lumi keeps, how it fades, and how it handles being contradicted.

Design → docs/architecture/memory.md / Interface → docs/interfaces/memory.md

| Module | What it owns |
|---|---|
| `records` | The types. `AssertionMode` and its ordering, `MemoryRecord`, `MemoryCandidate` |
| `decay` | Salience: the deterministic correction, the decay curve, the floor. **Pure** |
| `contradiction` | Which of two beliefs wins. **Pure, and decided in code rather than by an LLM** |
| `store` | The SQLite implementation. The only writer of memory records |

**This package does not delete.** Physical deletion of user data lives in
`lumi.storage.retention`, so that the boundary in docs/contracts/privacy.md §5 is one
file rather than a property spread across the codebase.
"""
