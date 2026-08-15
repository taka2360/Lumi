"""Permission — **権限判断の唯一の所有者。**

`Canonicalizer` / `BindVerifier` / `ResultVerifier` をここに置いているのは、
**所有者を物理的に明示するため**（docs/interfaces/tool.md）。`tools/` には置かない。

依存の向き: `tools → permission → kernel`。**逆はしない。**
"""
