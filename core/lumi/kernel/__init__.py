"""Lumi Kernel — holds only types and arbitration; knows nothing about concrete capabilities.

**Static checks guarantee that `kernel/` depends on no other module**
(docs/architecture/core.md §4). There are exactly two exceptions.

| Exception | Reason |
|---|---|
| `lumi.provenance` | `Signal` carries `trust_level`. Placing it under memory/ would create a reverse dependency |
| `lumi.logging` | Structured logging isn't a capability — it's the foundation every module sits on |

"Outside world" concerns like persistence are received through a Protocol
(`EventStore`). **The implementation lives outside the kernel.**
"""
