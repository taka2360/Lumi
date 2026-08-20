"""First-run setup — fetching, verifying, and detecting external engines.

Design → docs/architecture/setup.md / Decision → docs/decisions/ADR-019-tts-engine-distribution.md

**No external communication until the user chooses to.** The only place in this
package that touches HTTP is `install.py`'s `install_engine`, and it's only called
after receiving the user's choice.
"""
