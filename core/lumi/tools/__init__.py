"""Tool — the implementation of a capability. **Holds no judgment.**

A `Tool` implements only `bind` and `execute`.
Normalization (`Canonicalizer`), permission decisions (`decide`), and verification
(`BindVerifier`) all live in `permission/`, **owned by the Kernel**.

Dependency direction: `tools → permission → kernel`.
"""
