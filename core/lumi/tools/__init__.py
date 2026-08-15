"""Tool — 能力の実装。**判断は持たない。**

`Tool` が実装するのは `bind` と `execute` の2つだけ。
正規化（`Canonicalizer`）も権限判断（`decide`）も検証（`BindVerifier`）も
`permission/` にあり、**Kernel が所有する**。

依存の向き: `tools → permission → kernel`。
"""
