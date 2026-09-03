"""Dev-time scripts. **Never imported by Core, and not shipped** (the wheel packages
`lumi` only).

It is a package so that `scripts.llm_profile_eval` is one module name rather than two:
`tests/test_scripts_profile_eval.py` imports it to check the part of the A/B harness that
does not need an LLM — how a generation is classified — and without this file mypy maps
the same source to both `llm_profile_eval` and `scripts.llm_profile_eval`.

Running it directly still works: `uv run python scripts/llm_profile_eval.py`.
"""
