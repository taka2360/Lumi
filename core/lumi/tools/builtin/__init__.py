"""in-core built-in Tools (Class A).

**This isn't "putting a capability into Core."** It's here because the Kernel
execution contract (bind → verify → execute) only holds in-core (ADR-017). Since this
isn't third-party code, there's no motivation to isolate it either.
"""
