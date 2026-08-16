"""TTS. **A separate CPU process, over HTTP** (docs/interfaces/provider.md).

TTS never uses the GPU, so the LLM can claim all the VRAM.
The engine is LGPL-3.0-family, and **being a separate process talking over HTTP is
what keeps Core (MIT) within its license boundary** (docs/licensing.md).
"""
