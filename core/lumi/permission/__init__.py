"""Permission — **the sole owner of permission decisions.**

`Canonicalizer` / `BindVerifier` / `ResultVerifier` live here so that
**ownership is made physically explicit** (docs/interfaces/tool.md). Never in `tools/`.

Dependency direction: `tools → permission → kernel`. **Never the reverse.**
"""
