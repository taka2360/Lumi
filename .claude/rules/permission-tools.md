---
paths:
  - "core/lumi/permission/**/*.py"
  - "core/lumi/tools/**/*.py"
---

# Permission Kernel と Tool 実行

契約 → [contracts/tool-execution.md](../../docs/contracts/tool-execution.md), [architecture/permission.md](../../docs/architecture/permission.md), [interfaces/tool.md](../../docs/interfaces/tool.md), [ADR-006](../../docs/decisions/ADR-006-kernel-execution-contract.md), [ADR-017](../../docs/decisions/ADR-017-out-of-process-tool-contract.md)

## 契約

> **Policy が検査した対象と、execute が操作した対象は同一でなければならない。**

```
Class A (in-core):  canonicalize → decide → bind → verify → execute
                       Kernel     Kernel   Tool  Kernel   Tool
Class B (別プロセス): canonicalize → decide → invoke → verify_result
                       Kernel     Kernel   Ext      Kernel（事後）
```

| lane | Class |
|---|---|
| `fs` `process` `input` `desktop` `system` `memory` `character` | **A（in-core 限定）** |
| `browser` `game` `widget` | **B（out-of-process）** |

## Tool が実装するのは `bind` と `execute` の2つだけ

| 実装してはいけない | 理由 |
|---|---|
| `authorize()` | Tool の実装ミスがそのまま権限バイパスになる |
| `canonicalize()` | Kernel 所有。lane で選択される |
| `verify()` | Tool を信頼することになる |
| `PermissionKernel` の呼び出し | Invariant 1 |

`Canonicalizer` / `BindVerifier` / `ResultVerifier` は **`core/lumi/permission/` に置く。`core/lumi/tools/` ではない。** 所有者を物理的に明示する。

## Policy は `decide()` ひとつだけ

```python
def decide(base_risk, actor, effective_trust, grant) -> Decision
```

- **引数はこの4つだけ。** LLM の理由文・Tool の自己申告・Extension の `reason` を引数にしない
- **純粋関数**にする。テスト可能性と `policy_version` の意味がここに懸かっている
- `actor` 昇格を**先に**適用し、以降の規則は `effective_risk` に対して適用する
- `self_initiated` の L3 以上は**「厳しくなる」のではなく `DENIED`**。「1段上げる」ではない
- `Decision` を返す関数を他に作らない

登録時に落とすものを `decide()` に入れない（cancellation 制約 / Class B の risk 下限 / ハードコード不変条件）。**実行時に毎回判定するより、登録時に落とす方が fail-closed として強い。**

## 禁止事項

| # | 禁止 | 理由 |
|---|---|---|
| 1 | `execute` の中で生入力を再解決する（パス文字列の再利用、URL 文字列での再判断、PATH 解決の再実行） | TOCTOU |
| 2 | Tool が `PermissionKernel` を呼ぶ | Invariant 1 |
| 3 | `BindVerifier` を Tool 側に実装する | Tool を信頼することになる |
| 4 | ウィンドウをタイトル文字列で指定する | 文字列は容易に偽装される |
| 5 | `SecurityScope` を可変にする | 検査後に書き換わりうる |
| 6 | 正規化に失敗した入力を「よく分からないので通す」 | fail-open |

## fail-closed を徹底する

| 段階 | 失敗時 |
|---|---|
| canonicalize | `deny` |
| decide | `deny` / `ask` |
| bind | 実行しない |
| **verify** | **実行しない** |

**「よく分からないので通す」経路を作らない。**

## Class B について書いてはいけないこと

**「TOCTOU が防止されている」と書かない。** `ResultVerifier` は事後検証であり、副作用は既に起きている。
検出であって防止ではない。その分を `risk >= L3`（必ず `ask`）で埋めている。

## 登録時 fail-closed 検証

- [ ] メタデータの欠落 → 例外
- [ ] `lane` に対応する `Canonicalizer` / 検証器が未登録 → 例外
- [ ] Class A の lane を out-of-process Extension が提供 → 例外
- [ ] Class B かつ `side_effect != none` かつ `risk < L3` → 例外
- [ ] `non_cancellable` かつ `side_effect != none` かつ `risk < L3` → 例外

## ハードコード不変条件（設定で無効化できない）

Lumi 自身のウィンドウ・プロセス・設定ファイル・監査ログ / 権限プロンプト / 認証情報ストア /
ブラウザプロファイル / SSH・GPG 鍵。**canonicalize の段階で `deny` にし、`decide()` に到達させない。**

## 監査ログ

- **決定内容にかかわらず全件記録する**（deny も ask も）
- `policy_version` / `policy_rule_id` は**必須**。後から「なぜ許可したのか」に答えられなくなる
- `raw_input_digest` と `security_scope` の**両方**を記録する（正規化が正しかったかを後から検証するため）
- `audit_log` への `DELETE` / `UPDATE` をコードベースに存在させない
