# Wire 契約 — 線上に出る名前と定数

確定度: **Confirmed**（→ [ADR-022](../decisions/ADR-022-wire-contract.md)）

> **値の唯一の定義場所は [`wire.json`](wire.json)。** このファイルは規則だけを持ち、**値の表を持たない**。

Lumi は Python（Core）・TypeScript（Stage）・Rust（Shell）の3言語で書かれており、
3つは WS と Tauri IPC で繋がっている。線の両端で同じ文字列・同じ数値を書く必要があるが、
**片方だけ直しても何もエラーにならない。** しかも Lumi の実装は「知らない値は安全側に落とす」
（`?? null` / `?? "starting"` / `unknown_method`）ので、ずれは**静かな劣化**として現れる。

そこで、値を `wire.json` に集め、**3言語それぞれのテストが自分の定数をそれと突き合わせる。**
コード生成はしない（採らなかった理由 → [ADR-022](../decisions/ADR-022-wire-contract.md) の Alternatives）。

---

## 1. 何を載せるか

> **プロセス境界を越えて、文字列または数値として現れるものだけ。**

| 載せる | 載せない |
|---|---|
| WS の `method` 名、`PROTOCOL_VERSION` | タイムアウト・ポーリング間隔・再試行回数 |
| Tauri のイベント名・コマンド名 | ウィンドウの大きさの上限下限、倍率のステップ |
| 線に乗る enum の**値**（`installed` / `A` など） | 内部でしか使わない enum、Python 側の識別子名 |
| 線に乗る固定の選択肢（`install` / `skip`） | 失敗理由の識別子（`hash_mismatch` など。**下記参照**） |

**片側だけで完結する値を契約に入れない。** 契約が大きくなるほど、
「契約に載っているが実は片側にしかない」ものが混ざり、契約全体の信用が落ちる。

### 失敗理由（`reason`）を載せない理由

`SetupError.reason` は Stage の `FAILURE_TEXT` が日本語に直しているが、
**Stage は知らない `reason` を隠さずそのまま出す**（`取得に失敗しました（${reason}）`）。
つまり**ずれても劣化しない**設計になっている。契約に載せると、
Core 側で失敗理由を1つ増やすたびに3言語を触ることになり、**割に合わない。**

---

## 2. 誰がどれを持つか

各言語は `wire.json` を**実行時には読まない。** 自分の言語の定数を宣言し、
**テストのときだけ** `wire.json` と突き合わせる。実行時に読むと、
配布物に `docs/` を含める必要が出てしまう。

| | Core (Python) | Stage (TS) | Shell (Rust) |
|---|---|---|---|
| `protocol_version` | ✓ | ✓ | ✓ |
| `namespace_by_role` | ✓ | — | — |
| `methods.stage` | ✓ | ✓ | — |
| `methods.os` | — | — | ✓ |
| `setup_prompt_choices` | ✓ | ✓ | — |
| `tauri_events` | — | ✓ | ✓ |
| `tauri_commands` | — | ✓ | **✗**（下記） |
| `window_labels` | — | — | ✓ |
| `enums.*`（viseme 以外） | ✓ | ✓ | — |
| `enums.viseme` | ✓ | ✓ | — |

`—` は「その言語には現れない」。**`✗` は「現れるが検査できない」。**

---

## 3. 項目を足すとき

```
wire.json に足す
  → 各言語の定数に足す
  → 各言語のテストの突き合わせ対象に足す
```

順序を守らなくてもよい。**足し忘れればテストが落ちる**ので、落ちた場所が教えてくれる。

`wire.json` は `docs/contracts/` にあるので、**変更は設計変更である。**
新しい `method` を1つ足すだけでも、対応する `architecture/` か `interfaces/` の記述を伴うこと。

---

## 4. 保証しないこと

- **名前と値が一致することしか保証しない。** payload の**形**（フィールド名・型・必須性）は対象外。
  `TtsSetup.to_payload()` が `progress` を返すのに Stage が `percent` を読んでいる、
  という類の食い違いは**この契約では検出できない**
- **Rust の Tauri コマンド名は検査できない。** `#[tauri::command]` が付いた**関数名**が
  そのままコマンド名になるため、データとして取り出せない。
  `tauri_commands` は **Stage 側の片側検査**であり、
  Shell 側で関数を改名しても、このテストは落ちない（Stage の実行時に落ちる）
- **実行時の互換性を保証しない。** テストは開発時にしか走らない。
  配布物の中で版がずれないことは [ADR-021](../decisions/ADR-021-sidecar-packaging.md) の
  同時配布が担保しているのであって、この契約ではない
- **契約に載せ忘れたものは守られない。** §1 の線引きは人間が行う

---

## 5. テスト

| # | 内容 | 場所 |
|---|---|---|
| 1 | Core の `PROTOCOL_VERSION` / namespace / `stage.*` method / 選択肢 / enum 値が `wire.json` と一致する | `core/tests/test_wire_contract.py` |
| 2 | Stage の `PROTOCOL_VERSION` / イベント名 / コマンド名 / `stage.*` method / 選択肢 / enum 値が一致する | `stage/src/core/wire.test.ts` |
| 3 | Shell の `PROTOCOL_VERSION` / イベント名 / `os.*` allowlist / ウィンドウ label が一致する | `shell/src-tauri/src/wire_contract.rs` |
| 4 | **3言語すべてが `protocol_version` を検査している**（1つでも抜けるとその言語だけずれる） | 上記3つが各々検査する |
| 5 | `wire.json` の `methods.stage` は全て `stage.` で始まり、`methods.os` は全て `os.` で始まる | 1（Core 側で検査） |
