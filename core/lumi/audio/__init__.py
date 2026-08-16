"""Audio I/O. **Lives in Core** (keeps the barge-in critical path inside Core).

Design → docs/architecture/audio.md / ADR-003

Phase 0 only has playback. capture / VAD / EchoGuard belong to Phase 1.
"""
