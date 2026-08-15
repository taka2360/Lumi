# ADR-006: Kernel実行契約 — Canonicalizer / decide / BindVerifier を Kernel 所有にする

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../contracts/tool-execution.md](../contracts/tool-execution.md), [../architecture/permission.md](../architecture/permission.md), [../interfaces/tool.md](../interfaces/tool.md) |

---

## Decision

ツール実行を **5段の Kernel 実行契約**にする。

```
canonicalize → decide → bind → verify → execute
   Kernel      Kernel    Tool   Kernel    Tool
```

**`authorize` を Tool interface に置かない。** Tool が実装するのは `bind` と `execute` の2つだけ。

> **契約: Policy が検査した対象と、execute が操作した対象は同一でなければならない。**

---

## Reason

### Tool が authorize すると、実装ミスが権限バイパスになる

```python
# ✗ この設計だと…
class Tool:
    def authorize(self, raw_input) -> SecurityScope:
        return SecurityScope(canonical=raw_input["path"])   # 正規化を忘れた
```

これで `~/Documents/../../../Windows/System32` が通る。**Tool を1つ書き間違えるだけで穴が空く。**

Invariant 1（判断は Core だけ）を型で強制するには、authorize が Tool の外にある必要がある。

### `BindVerifier` が無いと Tool を信頼していることになる

`Tool.bind` は Tool が実装する。悪意ある（または実装ミスの）Tool が scope とは違う対象の Handle を返したら、`execute` はその対象を操作してしまう。

**`BindVerifier` は Kernel 所有で、Tool が返した Handle が本当に scope を指しているかを独立に検証する。**

これが無いと、5段のうち bind と execute の2段が検証されないまま Tool に委ねられる。

### TOCTOU は「可能なら」では防げない

正規化してチェックしても、実行までの間に対象がすり替わりうる。

| 攻撃 | 例 |
|---|---|
| symlink 張り替え | チェック後に `~/Documents/link` → `C:\Windows` に変更 |
| URL リダイレクト | `https://safe.com/r?to=evil.com` |
| DNS rebinding | 解決時と接続時で IP が変わる |
| ウィンドウのすり替え | 前面ウィンドウが変わる |

**「Handle を bind して、それだけを操作する」を義務にする。** 「可能なら」では実装者が判断を迫られ、必ずどこかで漏れる。

---

## Alternatives

### A. Tool が authorize を実装する（当初案）

**利点:** Tool の実装が自己完結する。Kernel が lane を知らなくてよい
**欠点:** **Tool の実装ミス = 権限バイパス。** レビュー負荷が Tool の数だけ増える

### B. Kernel が bind も実装する

**利点:** Tool が execute だけになり、さらに単純
**欠点:** bind にはドメイン知識が必要（fs は fd、browser は connection、input は HWND）。Kernel が全 lane の実装を持つことになり、lane 追加のたびに Kernel が変わる

### C. `BindVerifier` を置かない

**利点:** 実装が1段減る
**欠点:** **Tool が返した Handle を検証なしで信じることになる。** bind と execute の間に検証が無い

### D. 引数チェックのみ（正規化も bind も無し）

**利点:** 最も単純
**欠点:** traversal / symlink / redirect が全部素通り。**現実的でない**

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| lane ごとに Canonicalizer と BindVerifier が要る | 新 lane 追加のコストが上がる（片方だけでは登録できない） |
| Handle を引き回す実装 | fd / connection / PID / HWND のライフサイクル管理 |
| Kernel が lane を知る | `ScopeLane` enum が Kernel 側にある |

### 得るもの

- **Tool の実装ミスが権限バイパスにならない**
- TOCTOU が構造的に防がれる
- Tool のレビューが「bind と execute だけ」に集中できる
- 正規化ロジックが1箇所に集まり、攻撃ベクタのテストが1箇所で済む

---

## Consequences

### `Tool` interface が単純になる

```python
class Tool(Protocol):
    lane: ScopeLane                # 宣言
    permission: PermissionSpec     # 宣言
    def bind(self, ctx, scope: SecurityScope) -> Handle: ...
    async def execute(self, ctx, handle: Handle) -> ToolResult: ...
```

`authorize` / `canonicalize` / `verify` は**実装してはいけない**。静的検査で保証する。

### `SecurityScope` が不変になる

```python
@dataclass(frozen=True)
class SecurityScope:
    lane: ScopeLane
    canonical: str
    metadata: Mapping[str, Any]
```

Policy が検査した後に書き換わりうると TOCTOU が成立するため。

### fail-closed が徹底される

| 段階 | 失敗時 |
|---|---|
| canonicalize | `deny` |
| decide | `deny` / `ask` |
| bind | 実行しない |
| **verify** | **実行しない** |

**「よく分からないので通す」経路を作らない。**

### 起動時検証が増える

```
lane に対応する Canonicalizer が未登録 → 例外
lane に対応する BindVerifier が未登録  → 例外
```

新しい lane を足すには両方の実装が必須になる。これは意図した摩擦である。

### Invariant 8 の Core 側実装になる

`input` lane の `BindVerifier` が「bind した HWND が保護対象でないこと」を検証する。

Shell 側の拒否（B3）と**二重化**することで、片方の実装ミスで穴が空かないようにする。

### ネットワークの限界は残る

DNS rebinding に対して正規化は原理的に不完全。そのため `browser.*` は scope 単位だけでなく**ブラウザ Extension 全体の隔離**でも守る（多層防御）。

**単一の防御で完全性を主張しない。**

### AIRI の `computer-use-mcp` から借りたもの

ToolDescriptor のメタデータ必須 + fail-closed 検証という考え方。

AIRI は `lane` / `kind` / `readOnly` / `destructive` / `concurrencySafe` / `requiresApprovalByDefault` / `defaultDeferred` を必須メタデータとし、`validateDescriptor()` で検証、未登録ツールで例外を投げる（fail-closed）。**これは良い設計であり、考え方を採用する。**

ただし AIRI の承認フローは `computer-use-mcp` という独立した MCP サーバ内にのみ存在し、**AIRI 本体のプラグインシステムには接続されていない**。Lumi では Permission Kernel が唯一の経路になる。
