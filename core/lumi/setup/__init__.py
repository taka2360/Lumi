"""First-run setup — fetching, verifying, and detecting external engines.

Design → docs/architecture/setup.md / Decision → docs/decisions/ADR-019-tts-engine-distribution.md

**No external communication until the user chooses to.** Fetching lives in
`lumi.artifacts` (ADR-045) and runs only after the user's choice has been received.
What stays here is the judgement: what is missing, what to ask, and what the
answer means. The HTTP this package does hold is local only — the Ollama recheck
(`detect.py`) and the local model catalog (`ollama.py`), both 127.0.0.1.
"""
