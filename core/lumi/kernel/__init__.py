"""Lumi Kernel — 型と調停だけを持ち、具体的な能力を知らない。

**`kernel/` が他のモジュールに依存しないことを静的検査で保証する**
（docs/architecture/core.md §4）。例外は次の2つだけ。

| 例外 | 理由 |
|---|---|
| `lumi.provenance` | `Signal` が `trust_level` を持つ。memory/ に置くと逆依存になる |
| `lumi.logging` | 構造化ログは能力ではなく、全モジュールの土台 |

永続化のような「外の世界」は Protocol（`EventStore`）で受け取る。**実装は kernel の外**にある。
"""
