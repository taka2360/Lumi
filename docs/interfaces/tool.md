# Interface: Tool

> **Tool が実装するのは `bind` と `execute` の2つだけ。** 正規化も権限判断も検証もしない。

親: [DESIGN.md](../DESIGN.md) / 契約: [../contracts/tool-execution.md](../contracts/tool-execution.md)

---

## Tool

```python
class Tool(Protocol):
    # ── 静的な宣言 ─────────────────────────────
    name: str
    description: str
    input_schema: dict            # JSON Schema
    output_schema: dict

    lane: ScopeLane               # Canonicalizer / BindVerifier / ResultVerifier の選択
    kind: ToolKind                # read | write | control | workflow
    permission: PermissionSpec

    concurrency_safe: bool
    idempotent: bool
    deferred: bool                # 既定で LLM に露出しない

    # ── 実装するのはこの2つだけ（Class A のみ）───
    def bind(self, ctx: ToolContext, scope: SecurityScope) -> Handle: ...
    async def execute(self, ctx: ToolContext, handle: Handle) -> ToolResult: ...
```

> **上の `bind` / `execute` を持つのは Class A（in-core）の Tool のみ。**
> Class B（out-of-process Extension が提供する Tool）は Core 側では `RemoteToolDescriptor` として登録され、`bind` / `execute` を持たない。→ [§ Class A と Class B](#class-a-と-class-b)

### 実装してはいけないもの

| ✗ | 理由 |
|---|---|
| `authorize()` | Tool の実装ミスが権限バイパスになる（Invariant 1） |
| `canonicalize()` | Kernel 所有。lane で選択される |
| `verify()` | Tool を信頼することになる |
| `PermissionKernel` の呼び出し | Invariant 1 |

これらは静的検査で保証する。

---

## ToolContext

```python
@dataclass(frozen=True)
class ToolContext:
    cancel_token: CancelToken
    deadline: datetime
    actor: Actor                     # user_initiated | self_initiated | scheduled | system
    grant_handle: GrantHandle | None

    activity_id: ActivityId
    correlation_id: str
    idempotency_key: str | None      # 副作用を持つなら必須

    input_trust_level: TrustLevel    # 呼び出し元 context の effective_trust
```

### `cancel_token` の扱いは `permission.cancellation` で決まる

| 契約 | Tool の実装義務 |
|---|---|
| `cooperative` | `execute` 内で `cancel_token.is_set()` を定期的にチェックし、次のチェックポイントで安全に中断する |
| `hard` | 外部から強制終了できる構造にする（subprocess の kill、コネクション切断） |
| `non_cancellable` | チェックしない。ただし**実行時間の上限を守る** |

---

## PermissionSpec

```python
@dataclass(frozen=True)
class PermissionSpec:
    capability: str              # "fs.read" | "browser.navigate" | "shell.exec" | ...
    risk: Risk                   # L0 | L1 | L2 | L3 | L4
    reversible: bool
    side_effect: SideEffect      # none | local | external | irreversible
    cancellation: Cancellation   # cooperative | hard | non_cancellable
```

`lane` は `PermissionSpec` ではなく **`Tool` に持たせる**。lane は「どの Canonicalizer / BindVerifier を使うか」という実行機構の選択であって、権限の宣言ではないため。重複して持たせると齟齬の原因になる。

### 登録時の fail-closed 検証

起動時に例外を投げる条件:

| # | 条件 |
|---|---|
| 1 | メタデータの欠落 |
| 2 | `lane` に対応する `Canonicalizer` が未登録 |
| 3 | Class A: `lane` に対応する `BindVerifier` が未登録 |
| 4 | Class B: `lane` に対応する `ResultVerifier` が未登録 |
| 5 | **Class A の lane を out-of-process Extension が提供している** |
| 6 | **Class B かつ `side_effect != none` かつ `risk < L3`** |
| 7 | `cancellation == non_cancellable` かつ `side_effect != none` かつ `risk < L3` |

---

## SecurityScope

```python
@dataclass(frozen=True)         # immutable
class SecurityScope:
    lane: ScopeLane
    canonical: str               # 正規化済みの対象（絶対パス / 正規化 URL / 実行ファイル実体パス）
    metadata: Mapping[str, Any]  # lane 固有（HWND, PID など）
```

### 不変である理由

Policy が検査した後に scope が書き換わりうると、TOCTOU が成立する。

### 生成者

**`Canonicalizer`（Kernel 所有）のみ。** Tool も LLM も生成できない。

---

## Handle

```python
class Handle(Protocol):
    """SecurityScope が指す対象への安定した参照。"""
    scope: SecurityScope         # どの scope に対して bind されたか
    def close(self) -> None: ...
```

### lane 別の実体

| lane | Handle の実体 | `BindVerifier` の検証 |
|---|---|---|
| `fs` | `open()` の fd（symlink 非追従） | `fstat` の実体パスが `scope.canonical` と一致 |
| `browser` | 確立済みコネクション / page | **最終リダイレクト先**が scope 内 |
| `process` | PID | PID の実行ファイルパスが scope と一致 |
| `input` | HWND | execute 直前に前面ウィンドウ = HWND、**かつ保護対象でない** |

### 有効性

**`BindVerifier.verify()` を通るまで、Handle は有効ではない。** `Tool.bind` が返した直後の Handle を `execute` に渡してはならない（Kernel が間に入る）。

---

## ToolResult

```python
@dataclass(frozen=True)         # immutable
class ToolResult:
    ok: bool
    value: Any | None
    error: ToolError | None

    provenance_class: ProvenanceClass   # 通常 UNTRUSTED
    trust_level: TrustLevel             # 通常 TAINTED
```

### provenance は Core が付与する

Tool は `provenance` を自分で設定しない。**Tool Registry が付与する**（Tool の自己申告を信じない）。

外部から取得したデータなら `UNTRUSTED`。Core 内部の計算結果なら入力から join。

---

## ScopeLane

```python
class ScopeLane(Enum):
    # ── Class A: in-core のみ。Handle 契約 ──────
    FS        = "fs"
    PROCESS   = "process"
    INPUT     = "input"
    DESKTOP   = "desktop"       # screenshot 等の読み取り
    SYSTEM    = "system"
    MEMORY    = "memory"        # Lumi の記憶操作
    CHARACTER = "character"     # 表情・モーション

    # ── Class B: out-of-process。事後検証契約 ───
    BROWSER   = "browser"
    GAME      = "game"          # Phase 8
    WIDGET    = "widget"        # Phase 7


LANE_CLASS: dict[ScopeLane, ToolClass] = {...}   # Kernel が所有。Tool は宣言しない
```

**新しい lane を足すには `Canonicalizer` と、クラスに応じた検証器（`BindVerifier` または `ResultVerifier`）の実装が必須。** 片方だけでは登録できない（fail-closed）。

---

## Class A と Class B

契約と根拠 → [../contracts/tool-execution.md](../contracts/tool-execution.md), [ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)

| | **Class A** | **Class B** |
|---|---|---|
| 実行 | in-core | out-of-process Extension |
| Core 側の型 | `Tool`（`bind` / `execute` を持つ） | `RemoteToolDescriptor`（メタデータのみ） |
| 検証 | `BindVerifier`（**実行前**） | `ResultVerifier`（**実行後**） |
| 副作用ありの risk | 宣言どおり | **`risk >= L3` 固定** |

```python
@dataclass(frozen=True)
class RemoteToolDescriptor:
    """Class B。Core は実行しない。Extension Host に委譲する。"""
    name: str
    description: str
    input_schema: dict
    output_schema: dict

    lane: ScopeLane               # Class B の lane でなければ登録時に例外
    kind: ToolKind
    permission: PermissionSpec    # side_effect != none なら risk >= L3 が必須

    extension_id: str
    concurrency_safe: bool
    idempotent: bool
    deferred: bool


@dataclass(frozen=True)
class RemoteToolReply:
    """Extension が返すもの。"""
    ok: bool
    value: Any | None
    error: ToolError | None
    acted_on: str                 # 実際に操作した対象。ResultVerifier が検査する
```

**`acted_on` は Extension の自己申告である。** Kernel はこれを信じて検証するのではなく、**申告と scope が食い違ったら結果を捨てる**ために使う。申告が嘘であっても、Lumi の文脈に入らないことだけは保証される（副作用そのものは防げない）。

---

## Canonicalizer / BindVerifier / ResultVerifier（Kernel 所有）

```python
class Canonicalizer(Protocol):
    lane: ScopeLane
    def canonicalize(self, raw_input: Mapping[str, Any]) -> SecurityScope:
        """失敗したら CanonicalizationError を投げる（fail-closed）。"""


class BindVerifier(Protocol):
    """Class A。execute の前に検証する。副作用を起こさせない。"""
    lane: ScopeLane
    def verify(self, scope: SecurityScope, handle: Handle) -> None:
        """不一致なら BindVerificationError を投げる。execute しない。"""


class ResultVerifier(Protocol):
    """Class B。invoke の後に検証する。副作用は既に起きている可能性がある。"""
    lane: ScopeLane
    def verify(self, scope: SecurityScope, acted_on: str) -> None:
        """scope 外なら ResultVerificationError。結果を破棄し denied として記録する。"""
```

**これらは `core/lumi/permission/` に置く。`core/lumi/tools/` ではない。** 所有者を物理的に明示する。

---

## ToolRegistry

```python
class ToolRegistry:
    def register(self, tool: Tool) -> None:
        """Class A。fail-closed 検証。条件を満たさなければ例外。"""

    def register_remote(self, desc: RemoteToolDescriptor) -> None:
        """Class B。Extension の announce から呼ばれる。fail-closed 検証。"""

    async def invoke(self, tool_name: str, ctx: ToolContext,
                     raw_input: Mapping[str, Any]) -> ToolResult:
        """Class A: canonicalize → decide → bind → verify → execute
        Class B: canonicalize → decide → ext_host.invoke → verify_result
        呼び出し側はどちらかを意識しない。"""

    def list_exposed(self) -> list[ToolDescriptor]:
        """deferred=False のものだけ。LLM に見せる用。"""

    def search(self, query: str) -> list[ToolDescriptor]:
        """tool_search メタツールの実装。deferred も含む。"""
```

### `invoke` 以外から `Tool.execute` を呼ばない

静的検査で保証する（Invariant 2）。

---

## ツール数の爆発対策

| 仕組み | 内容 |
|---|---|
| `deferred: True` | 既定で LLM に露出しない |
| `tool_search` メタツール | LLM が「こういうことがしたい」と検索し、必要なツールだけロードする |

**ツールが50を超えても、常時プロンプトに載るのは十数個に保つ。**

AIRI の `computer-use-mcp` が同じ問題に `defaultDeferred` で対処しており、これは良い設計として借用する。

---

## Phase ごとの登録範囲

| Phase | 登録される Tool | Class |
|---|---|---|
| **1** | L0 のみ（`memory.recall`, `character.set_expression` など） | A |
| **4a** | `fs.*`（**in-core built-in**） | A |
| **4b** | `browser.*`（Playwright Extension） | **B** |
| **4c** | `computer.*`（**in-core built-in**。OS 特権は `os.*` で Shell に依頼） | A |
| **5** | `vision.observe` | A |
| **7** | `widget.*` | B |
| **8** | `game.*` | B |

**`fs` / `computer` が in-core なのは、Handle 契約が成立するのが in-core だけだから**（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)）。第三者コードではないので隔離の動機も無い。

**Phase 1 で L0 しか無くても、`invoke` の経路は本番と同じ。**
