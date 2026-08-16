"""TTS。**別プロセス・CPU・HTTP**（docs/interfaces/provider.md）。

LLM に VRAM を全振りするため、TTS は GPU を使わない。
エンジンは LGPL-3.0 系であり、**HTTP 越しの別プロセスであることが
Core（MIT）のライセンス境界を保つ前提**になっている（docs/licensing.md）。
"""
