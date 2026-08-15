# ADR-017: 副作用を持つ lane の Tool を in-core に置き、out-of-process には事後検証契約を適用する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-15 |
| 関連 | [../contracts/tool-execution.md](../contracts/tool-execution.md), [ADR-006](ADR-006-kernel-execution-contract.md), [ADR-005](ADR-005-extension-two-mechanisms.md), [../interfaces/tool.md](../interfaces/tool.md), [../interfaces/extension.md](../interfaces/extension.md) |

---

## Decision

**`BindVerifier` による検証が成立するのは、`Tool.bind` が返した Handle が Core のプロセス内に存在する場合だけである。**

この事実から、Tool を2つのクラスに分ける。

| | **Class A（Handle 契約）** | **Class B（事後検証契約）** |
|---|---|---|
| 実行 | **in-core** | out-of-process Extension |
| lane | `fs` / `process` / `input` / `desktop` / `system` / `memory` / `character` | `browser` / `game` / `widget` |
| bind | Core が実行。Handle は Core のプロセス内 | **無い** |
| verify | **`BindVerifier`（実行前）** | **`ResultVerifier`（実行後）** |
| TOCTOU | 構造的に防止される | **防止できない。検出のみ** |
| 副作用ありの risk | `PermissionSpec` の宣言どおり | **`risk >= L3` に固定**（登録時に fail-closed 検証） |

### 規則

1. **Class A の lane を out-of-process Extension が提供することはできない。** manifest 検証で拒否する（fail-closed）。
2. Class B では Core は `canonicalize → decide` までを行い、Extension には**正規化済み `SecurityScope` のみ**を渡す。プロセスを跨ぐ Handle（fd / HWND / PID）は渡さない。
3. Class B の Tool 結果には「実際に操作した対象」(`acted_on`) の申告を必須とし、Kernel の `ResultVerifier` が scope 内かを検証する。scope 外なら**結果を破棄し `denied` として記録する**。
4. **`ResultVerifier` は事後検証であり、副作用の発生を防止しない。** これを補うため、Class B かつ `side_effect != none` の Tool は `risk >= L3` に固定する（＝必ず `ask` を経る。`self_initiated` は `deny`）。
5. Class B Extension には OS レベルの封じ込め（作業ディレクトリ限定・不要な権限の剥奪）を配布時の要件とする。

### 帰結（ロードマップの変更）

| 当初 | 変更後 |
|---|---|
| Filesystem Extension（out-of-process） | **廃止。`fs.*` は in-core built-in Tool** |
| Computer Extension（out-of-process） | **廃止。`computer.*` は in-core built-in Tool**（OS 特権は `os.*` で Shell に依頼） |
| Browser Extension（out-of-process） | **変更なし。Class B として扱う** |

---

## Reason

### Handle はプロセスを跨げない

当初の `interfaces/extension.md` には次の記述があった。

```jsonc
{ "tool": "fs.read", "handle_hint": { "fd": 7 } }
```

**fd 7 は Core プロセスの fd であって、Extension プロセスでは別のものを指すか、存在しない。** Windows では `DuplicateHandle`、POSIX では `SCM_RIGHTS` が必要であり、WS / stdio では原理的に渡せない。

### Extension が自分で bind すると、契約が「Tool を信頼する」に退化する

Extension が `scope.canonical` から自分でパスを open するなら:

- Core の `BindVerifier` は Extension プロセス内の fd を `fstat` できない → **verify が実行不能**
- Extension が生の文字列から対象を再解決することになり、[tool-execution.md](../contracts/tool-execution.md) の禁止事項1（execute の中で再解決しない）を必ず犯す
- **B4 は「Extension を信頼しない」境界**であるにもかかわらず、TOCTOU 防御を Extension の実装品質に委ねることになる

ADR-006 が `BindVerifier` を Kernel 所有にした理由（Tool の実装ミスが権限バイパスにならないこと）が、out-of-process では丸ごと失われる。

### `fs` を in-core にしても失うものが無い

Extension を out-of-process にする理由は「第三者コードの隔離」である。**`fs.*` / `computer.*` は Lumi 自身が書く公式実装であり、第三者コードではない。** 隔離の動機が無いのに、隔離のコストとして最も重要な防御を失っていた。

加えて `computer.*`（screenshot / input injection）は結局 `os.*` を通じて Shell に依頼するため、out-of-process にしてもプロセスが1つ増えるだけで、権限の観点では何も減らない。

### `browser` は in-core にできない

Playwright は Node.js 生態系であり、ブラウザプロセスを従える大きなランタイムを持つ。これを Core に取り込むのは ADR-002（Python 単一プロセス）と衝突する。かつ**ブラウザこそ最も汚染されたデータを扱う**ため、隔離の動機が最も強い。

したがって `browser` は out-of-process のまま、**防御の性質が違うことを認めた上で**、別の契約を当てる。

---

## Alternatives

### A. 全 Tool を out-of-process にする（当初案）

**利点:** 機構が1つ。Extension の書き方が統一される
**欠点:** **`BindVerifier` が全 lane で機能しなくなる。** ADR-006 の契約が文書上のものになる

### B. Handle を跨プロセスで渡す（`DuplicateHandle` / `SCM_RIGHTS`）

**利点:** 契約を変えずに済む
**欠点:** WS / stdio では実現できず、プラットフォーム固有の IPC が必要になる。Extension が任意言語である前提（ADR-005）が崩れる。Windows と POSIX で別実装になる

### C. Extension 内に Kernel の検証コードを埋め込む（共有ライブラリ）

**利点:** out-of-process のまま verify できる
**欠点:** **検証者が被検証者と同じプロセスに居る**ため、悪意ある Extension は検証を無効化できる。多層防御に見えて実は防御になっていない。加えて「Extension は任意言語」が崩れる

### D. `browser` も含めて事前防止を諦め、全て事後検証にする

**利点:** 契約が1つで済む
**欠点:** `fs` / `input` で事前防止できるものを捨てることになる。**防御を最も弱い lane に合わせることになる**

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| Core に `fs` / `computer` の実装が入る | Core が「能力の実装を持たない」という表現からの逸脱（後述） |
| 契約が2つになる | Tool 実装者が Class A / B を意識する |
| Class B の副作用ツールが必ず `ask` になる | ブラウザでのフォーム送信等が毎回確認を経る |
| `ResultVerifier` の追加実装 | lane ごとに必要 |

### 「Core は能力の実装を持たない」との関係

[core.md](../architecture/core.md) の定義は維持する。`fs.*` / `computer.*` を in-core にすることは**能力の実装を Core に置くこと**ではあるが、判定基準（「これを外しても Lumi は Lumi か？」）とは別に、**「これを Core の外に出すと Kernel 実行契約が成立するか？」という制約が優先する**。

実装上は `core/lumi/tools/builtin/` に閉じ込め、Kernel（`core/lumi/kernel/`）からは依存しない。将来 out-of-process 化が必要になったら、Class B に降格させれば良い（risk が上がるだけで、契約は壊れない）。

### 得るもの

- **`BindVerifier` が実際に機能する**。ADR-006 の契約が実装可能になる
- 最も危険な `input` lane が in-core になり、Invariant 8 の Core 側二重化が実装可能になる
- 事前防止できない lane が明示され、その分を `ask` で埋めていることが説明できる

---

## Consequences

### `Tool` の登録時検証が増える

```
lane が Class A かつ Tool が out-of-process Extension 由来 → 例外
lane が Class B かつ ResultVerifier が未登録            → 例外
lane が Class B かつ side_effect != none かつ risk < L3  → 例外
```

### `ext.tool.invoke` から `handle_hint` を削除する

Extension が受け取るのは `SecurityScope` と `deadline` と `correlation_id` のみ。

### Extension manifest に lane の宣言制約が加わる

`capabilities.tools[].lane` が Class A なら、`runtime: out-of-process` の manifest はロードを拒否される。

### 「単一の防御で完全性を主張しない」は変わらない

Class B の `browser` は依然として DNS rebinding に対して不完全である（ADR-006）。事後検証・プロセス隔離・出力の untrusted 化・L3+ の強制 `ask` を重ねて守る。

**Class B の Tool について「TOCTOU が防止されている」と書かない。**

### この判断を見直す条件

- 第三者製の Class A Tool を許したくなったとき（→ Phase 9 の第三者 Provider 判断と同じ問題。ADR が必要）
- Core の肥大が実際に問題になったとき（→ Class B に降格させ、risk を上げる）
