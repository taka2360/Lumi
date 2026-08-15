# ADR-022: プロセス境界を越える名前と定数を `wire.json` に一元化し、3言語のテストで突き合わせる

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-16 |
| 関連 | [../contracts/wire.md](../contracts/wire.md), [../DESIGN.md](../DESIGN.md) §12, [../architecture/core.md](../architecture/core.md) §3, [../contracts/security-boundaries.md](../contracts/security-boundaries.md), [ADR-015](ADR-015-core-shell-boundary.md), [ADR-021](ADR-021-sidecar-packaging.md) |

---

## Decision

**プロセス境界を越えて文字列・数値として現れる名前と定数**（以下 wire 定数）の唯一の定義場所を
**`docs/contracts/wire.json`** とする。

1. **値の正は `wire.json`。** `docs/` の散文は名前に言及してよいが、**一覧表を持たない**（SSoT 規則2）
2. **コード生成はしない。** Core / Stage / Shell は**自分の言語で定数を宣言し続ける**。
   生成物をリポジトリに入れず、ビルドに生成手順を足さない
3. **3言語すべてにテストを置く。** 各言語のテストが、自分の定数と `wire.json` を突き合わせる。
   **1言語でも欠けると、その言語だけが静かにずれる**
4. **対象は境界を越えるものだけ。** モジュール内部の定数・チューニング値（`POLL_INTERVAL_S`、
   `SCALE_STEP`、`MIN_STAGE_SIZE` など）は対象外。**片側だけで完結する値を契約に入れない**
5. 項目を足すときは **`wire.json` → 3言語の定数 → 3言語のテスト** の順に触る。
   順序を守らなくても、**テストが落ちることで漏れが分かる**

---

## Reason

### 現に6組が二重に書かれている

| wire 定数 | 書かれている場所 |
|---|---|
| `PROTOCOL_VERSION = 1` | `transport/protocol.py` / `core/protocol.ts` / `ws_client.rs` |
| Tauri イベント名 2つ | `hover.rs`, `core_endpoint.rs` / `platform/tauri.ts`, `core/connection.ts` |
| Tauri コマンド名 4つ | `lib.rs` の `generate_handler!` / `platform/tauri.ts` |
| `stage.*` / `os.*` の method 名 | `setup/coordinator.py`, `greeting.py`, `os_command.rs` / `core/useCoreConnection.ts` |
| `TtsSetupState` / `EngineRuntime` / `BootPhase` | `setup/state.py` / `core/store.ts` |
| `Viseme` の値 | `providers/tts/viseme.py` / `character/lipsync.ts` |

すべてコメントで相互参照はされている（「対応する Stage 側の定数は〜」）。
**しかしコメントは実行されない。**

### 片方だけ直すと、静かに壊れる

これが決め手である。Lumi は「**黙って劣化しない**」を設計原則に置いているが、
wire 定数のずれは**その原則を守れない形で失敗する**。

**反例1 — `PROTOCOL_VERSION` を Core だけ 2 に上げる。**
Shell の `parse_command` は「プロトコルバージョンが違う」で**すべての command を捨てる**。
Core は result を待ち、10 秒後に `transport.command.timeout` を出す。
Shell のログには理由が出るが、**Stage には何も出ない**。ユーザーから見えるのは
「Lumi が反応しない」だけで、どこが壊れているかを示すものが画面に無い。

**反例2 — `Viseme` に値を1つ足す。**
Stage の `parseTimeline` は `VISEMES.find(...) ?? null` なので、
知らないビセームは**口を閉じる**に落ちる。エラーも警告も出ない。
「その音のときだけ口が動かない」という、再現条件の分かりにくい劣化になる。

**反例3 — `BootPhase` に値を足す。**
`toTtsSnapshot` は `phases.find(...) ?? "starting"` なので、
Stage は**ローディング画面のまま止まる**。fail-closed に倒しているのは正しいが、
倒れたことが誰にも伝わらない。

いずれも「知らない値は安全側に落とす」という**正しい実装**が、
ずれを**見えなくしている**。実装を直す話ではなく、ずれを起こさせない話である。

### なぜ今か

Phase 1 で `stage.*` の method は確実に増える（発話・表情・Widget・設定）。
`Emotion` enum も入る。**AIRI は `Emotion` を4箇所に重複定義して壊れている**
（[../DESIGN.md](../DESIGN.md) §10）。増えてから入れる方が高くつく。

---

## Alternatives

### A. 単一の IDL からコード生成する

**利点:** ずれが**構造的に不可能**になる。人間の規律に依存しない。項目追加が1箇所で済む
**欠点:** 3言語ぶんのジェネレータ（または1つの多言語ジェネレータ依存）が要る。
生成物をコミットするか否かの判断が要り、どちらにも欠点がある。ビルド手順が増える。
対象は約 30 個であり、**この規模に対して機構が重い**
（設計原則7「将来使うかもしれないで抽象層を足さない」）

### B. Python を正とし、ビルド時に TS / Rust を生成する

**利点:** ジェネレータが1つで済む。Core が権威という全体構造とも合う
**欠点:** **Core が Shell と Stage のソースを書くことになる。**
Core は Shell の実装を知らないはずで（[ADR-015](ADR-015-core-shell-boundary.md)）、
依存の向きが逆に見える。ビルド順序の制約も生まれる

### C. 現状維持（コメントの相互参照だけ）

**利点:** ゼロコスト。今すでにこうなっており、実際まだ壊れていない
**欠点:** **守られる保証がレビューしかない。** そしてレビューで漏れたときに、
上の反例のとおり**静かに壊れる**。壊れたことが分かるのは実際に触ったときで、
そのとき原因として wire 定数のずれを疑うのは難しい

### D. 実行時にネゴシエーションする（hello で互いの能力を送り合う）

**利点:** 版が違っても動く。分散システムの定石
**欠点:** **Core / Shell / Stage は必ず同時に配布される**（[ADR-021](ADR-021-sidecar-packaging.md)。
1つのインストーラに入る）。版が違う状況がそもそも存在しない。
**存在しない問題に機構を足すことになる**

---

## Trade-offs

### 受け入れるコスト

- **定数を1つ足すのに4箇所（`wire.json` + 3言語）を触る手間は減らない。**
  この決定が変えるのは「漏らしたときに気づけるか」だけである
- `wire.json` はコードから読まれるが `docs/` に置く。**docs が純粋に人間向けでなくなる**。
  値の正が設計側にある形を優先した
- Rust の Tauri コマンド名は `#[tauri::command]` の**関数名**であり、データとして検査できない。
  この項目だけ **Stage 側の片側検査**になる

### 得るもの

- ずれが**テストの失敗として**出る。レビューの注意力に依存しなくなる
- 線上に何が出ているかの**一覧が1つできる**。今はどのファイルを読めば全体が分かるかが無い
- [../contracts/authority-matrix.md](../contracts/authority-matrix.md) の静的検査16項目（**未実装**）の
  最初の1件が、動く形で入る

### 保証しないこと

- **名前と値が一致することしか保証しない。** payload の**形**（フィールド名・型・必須性）は対象外。
  `TtsSetup.to_payload()` と `toTtsSnapshot()` の食い違いは、**この契約では検出できない**
- **実行時の互換性を保証しない。** テストは開発時にしか走らない。
  配布物の中で版がずれる事態は、[ADR-021](ADR-021-sidecar-packaging.md) の同時配布が防いでいるのであって、この契約ではない
- **契約に載せ忘れた定数は守られない。** 対象の線引き（境界を越えるものだけ）は人間が行う

---

## Consequences

- `docs/contracts/wire.json`（値の正）と [`docs/contracts/wire.md`](../contracts/wire.md)（規則）を追加する。
  [../DESIGN.md](../DESIGN.md) §12 の SSoT 表に行が増える
- **`stage/src/core/store.ts` の `known` / `runtimes` / `phases` を関数内ローカルから
  module-level の export に上げる。** テストから見えるようにするため
  （副次的に、`toTtsSnapshot` のたびに配列を作り直さなくなる）
- **`stage/src/core/useCoreConnection.ts` の method 名を、オブジェクトリテラルのキーから
  名前つき定数に上げる。** Core 側の `METHOD_STATE` / `METHOD_PROMPT` と対称になる
- Phase 1 で `stage.*` と `Emotion` が増えるたびに `wire.json` を触る。
  **`Emotion` はこの機構に載せる**（AIRI の4箇所重複を構造的に避ける）
- Phase 4c で `os.input.*` / `os.capture.*` を実装するとき、allowlist は `wire.json` 経由で
  3言語に伝わる。**allowlist の追加が Core 側だけで完結しないこと**が、B3 の観点でも望ましい
