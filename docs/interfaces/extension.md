# Interface: Extension

親: [DESIGN.md](../DESIGN.md) / 設計: [../architecture/extension.md](../architecture/extension.md) / [ADR-005](../decisions/ADR-005-extension-two-mechanisms.md)

---

## Manifest

```jsonc
{
  "manifest_version": 1,
  "id": "lumi.sensor-desktop",
  "version": "0.1.0",
  "name": "Desktop Sensor",
  "description": "前面アプリ・アイドル時間・在席状態を観測します",

  "runtime": "out-of-process",     // "in-core" | "out-of-process" | "stage"
  "trust_level": "untrusted",      // "official" | "verified" | "untrusted"

  "entrypoint": {
    "in_core":        { "module": "lumi_provider_ollama" },
    "out_of_process": { "command": "python", "args": ["-m", "lumi_sensor_desktop"] },
    "stage":          { "esm": "./renderer-live2d.js" }
  },

  // 天井（ceiling）。実効権限 = これ ∩ policy ∩ user grant
  "capabilities": {
    // out-of-process が宣言できるのは Class B の lane のみ（browser / game / widget）
    "tools": [
      {
        "name": "browser.read",
        "risk": "L1",
        "lane": "browser",
        "scope_hint": "https://*",
        "reason": "会話中に言及されたページを読むため"
      }
    ],
    "sensors": [
      { "key": "user.present",       "ttl_ms": 60000 },
      { "key": "user.focus_app",     "ttl_ms": 30000 },
      { "key": "desktop.fullscreen", "ttl_ms": 30000 }
    ],
    "signals": ["sensor.*"],
    "hooks":   ["before_tool"]
  }
}
```

### 検証（fail-closed）

起動時にロードを拒否する条件:

| # | 条件 |
|---|---|
| 1 | schema 違反 |
| 2 | **`runtime: in-core` かつ `trust_level != official`** |
| 3 | 宣言された `lane` に対応する Canonicalizer / 検証器が未登録 |
| 4 | `entrypoint` に該当 runtime のエントリが無い |
| 5 | Hook が固定セット外 |
| 6 | **`runtime: out-of-process` かつ Class A の lane（`fs` / `process` / `input` / `desktop` / `system` / `memory` / `character`）を宣言している** |
| 7 | **Class B の tool が `side_effect != none` かつ `risk < L3`** |

6 と 7 の根拠 → [../contracts/tool-execution.md](../contracts/tool-execution.md), [ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)

### `reason` フィールド

**ユーザーへの説明用。同意 UI に表示する。Policy はこれを見ない**（Invariant 1）。

AIRI の `ModulePermissionSpec` にも `reason` / `label` があり、コメントに *"Human-facing explanation for consent/permission UI"* と書かれているが**その UI が存在しない**。Lumi は Phase 4a で必ず実装する。

---

## ライフサイクル

```
discover   → validate → consent → load → announce → ready → (実行) → unload
                          ★
```

### ★ consent を飛ばさない

**AIRI はここが無い。** `permissionResolver` 未指定で manifest がそのまま granted になる。

```ts
// AIRI: packages/plugin-sdk/src/plugin-host/core.ts
const resolvedGrant = await this.permissionResolver?.({...}) ?? options.manifest.permissions
//                                                          ↑ 同意なしで全部許可
```

Lumi では consent 無しに ready にならない。同意結果は `extensions.granted_permissions_json` に永続化し、**manifest が変わったら再同意を求める**。

### 実効権限

```
effective = manifest_ceiling ∩ policy ∩ user_grant
```

3つすべてを満たすものだけが許可される（Invariant 5）。交差アルゴリズムの考え方は AIRI から借用する（キーは狭い方、アクションは両方に存在するものだけ、末尾ワイルドカード対応）。

---

## Protocol（`ext.*` namespace）

WS または stdio。**Extension が送れるのは Signal と Response のみ。**

| 方向 | メッセージ | 内容 |
|---|---|---|
| Core → Ext | `ext.lifecycle.ready` | 初期化完了通知 |
| Core → Ext | `ext.lifecycle.shutdown` | 停止指示 |
| Core → Ext | `ext.tool.invoke` | ツール呼び出し。**正規化済み `SecurityScope` のみ。Handle は渡さない** |
| Ext → Core | `ext.tool.result` | 結果 + `acted_on`。**Core が `provenance = untrusted` を付与する** |
| Ext → Core | `ext.sensor.push` | **Signal**。Core が検証して World facet を更新 |
| Ext → Core | `ext.announce` | 提供する tool / sensor の登録（`RemoteToolDescriptor` として） |

### Extension は DomainEvent を送れない

**プロトコルに経路が存在しない**（[../contracts/event-model.md](../contracts/event-model.md)）。静的検査で保証する。

理由: 外部が DomainEvent を書けると、Core が解釈していない状態変更が履歴に残る（Invariant 6 違反）。

### `ext.tool.invoke` の引数

Core が `canonicalize → decide` まで済ませた上で呼ぶ。Extension が受け取るのは:

```jsonc
{
  "tool": "browser.read",
  "scope": { "lane": "browser", "canonical": "https://example.com/docs/a" },
  "deadline_ms": 5000,
  "correlation_id": "..."
}
```

**Handle（fd / HWND / PID）は渡さない。渡せない。**
fd 7 は Core プロセスの fd であって、Extension プロセスでは別のものを指すか存在しない。プロセスを跨ぐ Handle 受け渡しは WS / stdio では原理的にできず、これが Class A / Class B を分けた理由そのものである（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)）。

**Extension が生の引数から対象を再解決しない。** 受け取った `scope.canonical` のみを操作する。ただし Core はこれを**強制できない**（別プロセスだから）。したがって:

### `ext.tool.result` は `acted_on` を必須とする

```jsonc
{
  "ok": true,
  "value": "...",
  "acted_on": "https://example.com/docs/a"   // 実際に操作した対象。必須
}
```

Kernel の `ResultVerifier` が `acted_on` と `scope` を照合し、**scope 外なら結果を破棄して `denied` として記録する**。

> **これは事後検証であり、副作用の防止ではない。** 補償として、Class B かつ `side_effect != none` の Tool は `risk >= L3` に固定される（＝必ず `ask`、`self_initiated` は `deny`）。

---

## Provider Extension（in-core）

```python
class Provider(Protocol):
    id: str
    kind: ProviderKind
    async def load(self) -> None: ...
    async def unload(self) -> None: ...
    def resource_hint(self) -> ResourceHint: ...
```

詳細 → [provider.md](provider.md)

### `in-core ⟹ official` の意味

in-core Provider は **Core のプロセス権限をそのまま持つ**。`untrusted + in-core` が存在すると、Extension の安全設計全体が無意味になる。

manifest 検証でこの組み合わせを拒否する（fail-closed）。

**第三者製 Provider を許すかは Phase 9 で判断する。** 許す場合は out-of-process Provider という第3の機構が必要になり、レイテンシとのトレードオフを再検討する。

---

## Capability Extension（out-of-process）

**別プロセス。任意言語。** Python である必要はない。

```
Core ←── WS (127.0.0.1) or stdio ──→ Extension プロセス
```

### 実装例

| Extension | 提供するもの | Phase |
|---|---|---|
| `sensor-desktop` | `user.*` / `desktop.*` / `system.*` の sensor（tool ではない） | 3 |
| `browser` | `browser.navigate` / `browser.click` / `browser.read`（Playwright, Class B） | 4b |

**`filesystem` / `computer` は Extension にしない。** Class A の lane であり、in-core built-in Tool として実装する（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)）。

### 障害時

→ [../architecture/extension.md](../architecture/extension.md) §6

**Extension の障害で Lumi 本体が止まってはならない。**

---

## Hook

**Hook の一覧と veto 可否は [../contracts/event-model.md](../contracts/event-model.md) が唯一の定義場所。** 固定セットのみで、増やすには ADR を要求する。

Extension 側の契約:

- 宣言できるのは manifest の `capabilities.hooks` に書いた Hook だけ
- **戻り値は `Continue` か `Veto(reason)` のみ。** 状態を返して Core がそれを適用する、という設計にはしない
- **観測と拒否はできるが、任意の状態書き換えはできない**（Invariant 6）

---

## Content Pack（コードなし）

構成と根拠 → [../architecture/extension.md](../architecture/extension.md) §9

interface 上の契約: **読み込み時にコードファイル（`.py` / `.js` / 実行可能ファイル）が含まれていたら拒否する。**

---

## 信頼レベル〔manifest 欄は予約。実装は Phase 9〕

一覧と既定の扱い → [../architecture/extension.md](../architecture/extension.md) §4

manifest 上の契約:

- `trust_level` フィールドは manifest_version 1 から**必須**（後方互換のため今予約する）
- **`runtime: in-core` かつ `trust_level != official` はロードを拒否する**（fail-closed）
- Phase 9 まで、全ての Capability Extension は `untrusted` として扱う（自分で書いたものも含む）

---

## テスト

| # | テスト |
|---|---|
| 1 | **`untrusted + in-core` の manifest がロード時に拒否される** |
| 2 | schema 違反の manifest が拒否される |
| 3 | 未登録 lane を宣言した manifest が拒否される |
| 4 | 固定セット外の Hook を宣言した manifest が拒否される |
| 5 | **consent なしに Extension が ready にならない** |
| 6 | manifest が変わったら再同意を求める |
| 7 | 実効権限が3つの交差になる |
| 8 | 宣言外の capability の使用が拒否される |
| 9 | 宣言外の sensor key の push が拒否される |
| 10 | Extension プロセスの異常終了で Core が落ちない |
| 11 | Extension のタイムアウトで該当 tool のみ失敗し、他は動き続ける |
| 12 | **Extension プロトコルに DomainEvent を送る経路が無い**（静的検査） |
| 13 | Extension の出力に Core が `provenance = untrusted` を付与する |
| 14 | Hook の veto がツール実行を止める |
| 15 | Hook が Core の状態を直接書き換えられない |
| 16 | Content Pack にコードファイルが含まれていたら拒否される |
| 17 | **Class A の lane を宣言した out-of-process manifest が拒否される** |
| 18 | **`ext.tool.invoke` のペイロードに Handle が含まれない**（静的検査） |
| 19 | **`acted_on` が scope 外の結果が破棄され、`denied` として監査に残る** |
| 20 | **Class B かつ副作用ありの tool が `risk < L3` なら announce が拒否される** |
