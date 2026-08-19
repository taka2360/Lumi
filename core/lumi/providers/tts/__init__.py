"""TTS. **A separate process, over HTTP** (docs/interfaces/provider.md).

ADR-025 prefers GPU when CUDA is available and falls back to CPU; CPU can be forced.
The engine is LGPL-3.0-family, and **being a separate process talking over HTTP is
what keeps Core (MIT) within its license boundary** (docs/licensing.md).
"""
