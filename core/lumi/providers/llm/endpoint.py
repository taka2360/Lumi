"""Where the local LLM runtime listens. **Depends on nothing.**

Decision → docs/decisions/ADR-023-llm-runtime-and-model-acquisition.md /
docs/decisions/ADR-045-core-module-layering.md

Two constants, in their own module because **three packages need the address and
none of them should need the client to learn it**: the Provider that streams
completions, setup's liveness probe, and setup's consented model pull. Reading
them from `providers/llm/ollama.py` meant importing an HTTP client and a
streaming parser to find out a port number.

**Not a setting.** To point Lumi at a different runtime, swap the whole Provider
(ADR-023) — a configurable host would turn "local inference" into "inference
wherever this string points", which is the one property the design promises.
"""

from __future__ import annotations

from typing import Final

#: **Loopback only.** Never a name that resolves off-machine.
HOST: Final = "127.0.0.1"

#: Ollama's own default. Lumi does not move it.
DEFAULT_PORT: Final = 11434
