# Kernel 実行契約 — canonicalize → decide → bind → verify → execute

> **Status: Confirmed**
> **契約: Policy が検査した対象と、execute が操作した対象は同一でなければならない。**

親: [DESIGN.md](../DESIGN.md) / 関連: [invariants.md](invariants.md), [state-machines.md](state-machines.md), [../architecture/permission.md](../architecture/permission.md), [../interfaces/tool.md](../interfaces/tool.md)

> **この契約は Class A（in-core / Handle 契約）の Tool に適用される。**
> out-of-process Extension が提供する Tool（Class B）には Handle が存在しないため、別の契約が適用される。→ [§ Class A と Class B](#class-a-と-class-b) / [ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)

---

## 解決したい問題 — TOCTOU

「引数をチェックしてから実行する」だけでは、**チェックした対象と操作する対象がすり替わりうる**。

| 攻撃 | 例 |
|---|---|
| パストラバーサル | `~/Documents/../../../Windows/System32/...` |
| シンボリックリンク | `~/Documents/link` → `C:\Windows` |
| **TOCTOU** | チェック後・実行前に symlink を張り替える |
| URL リダイレクト | `https://safe.com/r?to=evil.com` |
| URL 短縮 | `https://bit.ly/xxx` |
| DNS rebinding | 解決時と接続時で IP が変わる |
| ホモグラフ | `exаmple.com`（キリル文字の а） |
| UNC パス | `\\attacker\share\...` |

「可能なら handle を引き回す」では不十分。**全ての副作用ツールに義務付ける契約とする。**

---

## 誰が何を持つか

> **`authorize` は Tool interface に置かない。**

Tool が自分で authorize すると、**Tool の実装ミスがそのまま権限バイパスになる**。Invariant 1（判断は Core だけ）を型で強制するには、authorize が Tool の外にある必要がある。

### Tool が持つもの

```python
class Tool(Protocol):
    lane: ScopeLane                    # どの Canonicalizer / BindVerifier を使うか（宣言）
    permission: PermissionSpec         # 静的な宣言

    def bind(self, ctx: ToolContext, scope: SecurityScope) -> Handle: ...
    def execute(self, ctx: ToolContext, handle: Handle) -> ToolResult: ...
```

**Tool が実装するのは `bind` と `execute` の2つだけ。** 正規化も権限判断も検証もしない。

### Kernel が持つもの

```python
class ToolRegistry:
    canonicalizers:   dict[ScopeLane, Canonicalizer]    # Kernel 所有
    bind_verifiers:   dict[ScopeLane, BindVerifier]     # Kernel 所有（Class A）
    result_verifiers: dict[ScopeLane, ResultVerifier]   # Kernel 所有（Class B）
    permission:       PermissionKernel                  # Kernel 所有
```

---

## 実行フロー

```python
async def invoke(self, tool: Tool, ctx: ToolContext, raw_input: dict) -> ToolResult:
    # 1. 正規化 — Kernel 所有
    scope = self.canonicalizers[tool.lane].canonicalize(raw_input)
    #    失敗したら deny（fail-closed）

    # 2. 権限判断 — Kernel 所有。Tool は関与しない
    #    decide() の定義は architecture/permission.md（唯一の定義場所）
    #    ハードコード不変条件は 1. の canonicalize 段階で既に deny 済み
    #    cancellation / Class B の risk 制約は「登録時」に検証済みなのでここには無い
    decision = self.permission.decide(
        base_risk=tool.permission.risk,
        actor=ctx.actor,
        effective_trust=ctx.input_trust_level,
        grant=self.grants.find(tool.permission.capability, scope),
    )
    if decision is not ALLOW:
        return self._denied_or_ask(decision)      # state: denied

    # 3. bind — Tool が実装（ドメイン知識が必要）
    handle = tool.bind(ctx, scope)
    #    state: bound（ただし verify を通るまで有効ではない）

    # 4. verify — Kernel 所有 ★ 契約の要
    self.bind_verifiers[tool.lane].verify(scope, handle)
    #    失敗したら bind_failed。execute しない

    # 5. execute — Tool が実装。handle にのみ操作
    return await tool.execute(ctx, handle)
```

```
Raw Input
   ↓  Canonicalizer[lane]     Kernel所有。realpath / URL正規化 / IDN / リダイレクト事前解決
SecurityScope (immutable)
   ↓  PermissionKernel.decide  Kernel所有。Toolは関与しない
Decision
   ↓  Tool.bind               Toolが実装。ドメイン知識が必要
Handle
   ↓  BindVerifier[lane]      Kernel所有。「このhandleは本当にこのscopeか」★
   ↓  Tool.execute            Toolが実装。handleにのみ操作
ToolResult
```

---

## `BindVerifier` が決め手

**これが無いと、Tool を信頼していることになる。**

`Tool.bind` は Tool が実装する。もし Tool が悪意ある（または実装ミスで）scope とは違う対象の Handle を返したら、`execute` はその対象を操作してしまう。

**`BindVerifier` は Kernel 所有で、Tool が返した Handle が本当に scope を指しているかを独立に検証する。**

### lane 別の実装

| lane | Canonicalizer（Kernel） | Tool.bind | **BindVerifier（Kernel）** |
|---|---|---|---|
| `fs` | realpath 解決 / traversal 除去 / UNC 拒否 / 正規化後の絶対パス化 | `open()` で fd 取得（symlink 非追従） | **`fstat` から得た実体パスが scope と一致するか** |
| `browser` | URL 正規化 / IDN 正規化 / 短縮 URL 展開 / スキーム検証 | 接続確立 | **最終リダイレクト先が scope 内か。外なら中断** |
| `process` | 実行ファイルの実体パス解決 / PATH 解決の固定 | プロセス起動 → PID | **PID の実行ファイルパスが scope と一致するか** |
| `input` | 対象ウィンドウの同定 | HWND 取得 | **execute 直前に前面ウィンドウ = HWND か、かつ保護対象でないか** |
| `system` | （lane 固有） | （lane 固有） | （lane 固有） |

### `input` lane が二重防御である理由

`input` lane の verifier は **Invariant 8 の Core 側の実装**。Shell 側の拒否（[security-boundaries.md](security-boundaries.md) の B3）と**二重**にすることで、片方の実装ミスで穴が空かないようにする。

| 層 | 実装 |
|---|---|
| Core | `BindVerifier` が bind した HWND が保護対象でないことを検証 |
| Shell | 対象ウィンドウが保護対象なら無条件拒否。**Core の指示内容を見ない** |

---

## Class A と Class B

> **`BindVerifier` が成立するのは、Handle が Core のプロセス内に存在する場合だけである。**

fd / HWND / PID / コネクションは**プロセスを跨げない**。Extension が別プロセスで自分で bind するなら、Core はその Handle を検証できず、契約は「Tool を信頼する」に退化する。これは ADR-006 が排除したはずのものである。

そこで Tool を2クラスに分ける。→ 根拠と選択肢 [ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)

| | **Class A（Handle 契約）** | **Class B（事後検証契約）** |
|---|---|---|
| 実行 | **in-core** | out-of-process Extension |
| lane | `fs` / `process` / `input` / `desktop` / `system` / `memory` / `character` | `browser` / `game` / `widget` |
| フロー | canonicalize → decide → **bind → verify** → execute | canonicalize → decide → invoke → **verify_result** |
| Handle | Core のプロセス内 | **無い** |
| TOCTOU | **構造的に防止される** | **防止できない。検出のみ** |
| 副作用ありの risk | `PermissionSpec` の宣言どおり | **`risk >= L3` に固定**（登録時に fail-closed 検証） |

### 規則

| # | 規則 |
|---|---|
| 1 | **Class A の lane を out-of-process Extension が提供することはできない。** manifest 検証で拒否する |
| 2 | Class B に渡すのは**正規化済み `SecurityScope` のみ**。プロセスを跨ぐ Handle は渡さない |
| 3 | Class B の結果は「実際に操作した対象」`acted_on` の申告を必須とする |
| 4 | Kernel の `ResultVerifier` が `acted_on` を検証し、scope 外なら**結果を破棄して `denied` として記録する** |
| 5 | **Class B かつ `side_effect != none` なら `risk >= L3` に固定する**（事前防止できない分をユーザー確認で埋める） |

### Class B の実行フロー

```python
async def invoke_remote(self, tool: Tool, ctx: ToolContext, raw_input: dict) -> ToolResult:
    scope = self.canonicalizers[tool.lane].canonicalize(raw_input)     # 1. Kernel 所有
    decision = self.permission.decide(...)                             # 2. Kernel 所有
    if decision is not ALLOW:
        return self._denied_or_ask(decision)

    reply = await self.ext_host.invoke(tool, scope, ctx.deadline)      # 3. 別プロセス

    self.result_verifiers[tool.lane].verify(scope, reply.acted_on)     # 4. Kernel 所有 ★事後
    #    失敗したら結果を破棄し denied。副作用は既に起きている可能性がある

    return self._with_provenance(reply, UNTRUSTED)
```

### `ResultVerifier` は防止しない

| | `BindVerifier` | `ResultVerifier` |
|---|---|---|
| タイミング | **execute の前** | invoke の**後** |
| 効果 | **副作用を起こさせない** | 副作用は起きた。**結果を Lumi の文脈に入れない**だけ |
| 例（browser） | — | 最終 URL が scope 外 → 取得内容を破棄し、監査に記録 |

> **Class B の Tool について「TOCTOU が防止されている」と書かない。** 検出であり、防止ではない。

### Class B が依存する多層防御

事前防止が無い分、次を重ねる。

1. Extension プロセスの隔離（B4）
2. 出力を必ず `untrusted`（[provenance.md](provenance.md)）
3. 副作用ありは `risk >= L3` → 必ず `ask`、`self_initiated` は `deny`
4. `ResultVerifier` による事後検出と監査

---

## 禁止事項（実装レビューのチェック項目）

| # | 禁止 | 理由 |
|---|---|---|
| 1 | `execute` の中で生入力を再解決する（パス文字列の再利用、URL 文字列での再判断、PATH 解決の再実行） | TOCTOU |
| 2 | Tool が `PermissionKernel` を呼ぶ | Invariant 1 |
| 3 | `BindVerifier` を Tool 側に実装する | Tool を信頼することになる |
| 4 | ウィンドウをタイトル文字列で指定する | 文字列は容易に偽装される |
| 5 | `SecurityScope` を可変にする | 検査後に書き換わりうる |
| 6 | 正規化に失敗した入力を「よく分からないので通す」 | fail-open |

### 静的検査

```
- Tool 実装が PermissionKernel を import していない
- Tool 実装が Canonicalizer / BindVerifier を実装していない
- ToolRegistry.invoke 以外から Tool.execute が呼ばれていない
- SecurityScope が frozen dataclass である
```

---

## 失敗時の扱い — すべて fail-closed

| 段階 | 失敗時 | Tool 状態 |
|---|---|---|
| canonicalize | `deny` | `denied` |
| decide | `deny` または `ask`（ユーザーが拒否したら `deny`） | `denied` |
| bind | 実行しない | `bind_failed` |
| **verify** | **実行しない** | `bind_failed` |
| execute | 結果なし | `failed` |

**「よく分からないので通す」経路を作らない。**

---

## ネットワークの原理的な限界

DNS rebinding に対して正規化は**原理的に不完全**である。canonicalize 時に解決した IP と、接続時の IP が違いうる。

そのため `browser.*` は scope 単位だけでなく、**ブラウザ Extension 全体の隔離**でも守る（多層防御）。

- Browser Extension は out-of-process（B4）
- Extension の出力は必ず `untrusted`（[provenance.md](provenance.md)）
- 取得した内容が tool call を誘発しても、L3+ は `ask` に強制昇格（Invariant 3, 7）

**単一の防御で完全性を主張しない。**

---

## Tool メタデータ（fail-closed 検証）

AIRI の `computer-use-mcp` の ToolDescriptor レジストリ（メタデータ必須 + fail-closed）の考え方を採用する。

**`Tool` の型定義は [../interfaces/tool.md](../interfaces/tool.md) が唯一の定義場所。** ここでは契約としての検証条件のみを定める。

**起動時に例外を投げる条件（fail-closed）:**

| # | 条件 |
|---|---|
| 1 | メタデータの欠落 |
| 2 | `lane` に対応する `Canonicalizer` が未登録 |
| 3 | **Class A**: `lane` に対応する `BindVerifier` が未登録 |
| 4 | **Class B**: `lane` に対応する `ResultVerifier` が未登録 |
| 5 | **Class A の lane を out-of-process Extension が提供している** |
| 6 | **Class B かつ `side_effect != none` かつ `risk < L3`** |
| 7 | `permission.cancellation == non_cancellable` かつ `side_effect != none` かつ `risk < L3` |

ツール数の爆発対策（`deferred` / `tool_search`）→ [../interfaces/tool.md](../interfaces/tool.md)

---

## テスト

これらは **LLM を呼ばずにテストできなければならない。**

| # | テスト |
|---|---|
| 1 | **`BindVerifier` が scope と handle の不一致を検出する**（意図的に嘘の handle を返すモック Tool で検証） |
| 2 | 正規化の攻撃ベクタ: traversal / symlink / UNC / IDN homograph / URL redirect / 短縮 URL |
| 3 | 正規化失敗が `deny` になる（fail-closed） |
| 4 | verify 失敗時に `execute` が呼ばれない |
| 5 | `SecurityScope` が不変である |
| 6 | Tool が `PermissionKernel` を呼べない（静的検査） |
| 7 | メタデータ欠落 Tool の登録が起動時に例外になる |
| 8 | `non_cancellable` + 副作用ありの Tool が L3 未満で登録できない |
| 9 | `input` lane の verify が保護対象ウィンドウを拒否する |
| 10 | `browser` lane の `ResultVerifier` が scope 外の最終 URL を検出し、結果を破棄する |
| 11 | **Class A の lane を宣言した out-of-process Extension の manifest がロードを拒否される** |
| 12 | **Class B かつ副作用ありの Tool が L3 未満で登録できない** |
| 13 | `ext.tool.invoke` のペイロードに Handle（fd / HWND / PID）が含まれない（静的検査） |
