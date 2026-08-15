"""音声 I/O。**Core に置く**（barge-in の critical path を Core 内に閉じる）。

設計 → docs/architecture/audio.md / ADR-003

Phase 0 に在るのは再生だけ。capture / VAD / EchoGuard は Phase 1。
"""
