# ADR-050: デスクトップ Sensor は Shell に置き、out-of-process Sensor Extension を Phase 3 で作らない

| | |
|---|---|
| Status | Accepted |
| Date | 2026-09-06 |
| 関連 | [../roadmap.md](../roadmap.md) 未確定事項 14 / Phase 3, [../architecture/world-state.md](../architecture/world-state.md) §5, [../architecture/extension.md](../architecture/extension.md), [../interfaces/extension.md](../interfaces/extension.md), [../contracts/authority-matrix.md](../contracts/authority-matrix.md), [../contracts/event-model.md](../contracts/event-model.md), [ADR-017](ADR-017-out-of-process-tool-contract.md) |
| 実装 | 〔Phase 3〕`shell/src-tauri/src/sensor.rs`, `core/lumi/world/` |

## Decision

**Phase 3 の `user.*` / `desktop.*` / `audio.playing` / `system.*` は、Shell が読んで Signal として Core に送る。**
`sensor-desktop` を out-of-process Capability Extension として作らない。

| | |
|---|---|
| 誰が OS を読むか | **Shell**（Rust）。`hover.rs` と同じ形のポーリング監視スレッド |
| Core への伝え方 | **`Signal`**。[event-model.md](../contracts/event-model.md) の経路（認証 → schema 検証 → capability 検査 → Core が facet 更新 → DomainEvent 発行）は変えない |
| WorldFacet を更新するのは | **Core だけ**（Invariant 6）。Shell は素材を送るだけで、facet も TTL も confidence も触らない |
| ウィンドウタイトル | **読まない。** [world-state.md](../architecture/world-state.md) §5 のプライバシー規則をそのまま Shell 側の制約として持つ |
| 権限判断 | **Shell は持たない**（Invariant 1）。送る facet の集合は Shell のコードに固定で、実行時に増えない |

**out-of-process Capability Extension の機構そのものは捨てない。** 最初にそれを本当に必要とするのは
**Phase 8 の GameAgent** であり（[ADR-017](ADR-017-out-of-process-tool-contract.md)）、
manifest 署名・信頼レベル・インストール UI は Phase 9 に置いてある。**そこで作る。**

**[authority-matrix.md](../contracts/authority-matrix.md) は変更しない。** 表は Shell に
「World読み取り = OS読み取り（Sensorとして）」と「Signal送出 ✓」をすでに与えており、
この決定は表の中で動いている。

## Reason

**未確定事項 14 は「表とアーキテクチャのどちらが間違っているか」という形で立っていたが、
実際にはどちらも間違っていなかった。** 表の `OS特権` 列の定義は
**screenshot / input injection / window create / process launch** であり、
foreground app 名と idle 時間はそこに入っていない。矛盾していたのは**言葉づかいだけ**である。

決め手になったのは、矛盾の解消ではなく**実装の順序**のほうだった。

**Phase 3 の時点で Extension ホストが1行も無い。** `Signal` は
`kernel/command.py` のコメントに概念があるだけで型が無く、`core/lumi/extensions/` も存在しない。
`sensor-desktop` を out-of-process にすると、Phase 3 は
**「プロセス生成・IPC・manifest 解析・capability 検査」を新規に作り、その上で最初の利用者が
foreground app 名を 30 秒ごとに送るだけのプロセス**になる。

**そして次の利用者が Phase 8 まで現れない。** Phase 5 の Vision は in-core ツール、
Phase 7 の Widget は Broker という別機構である。つまりこの基盤は
**Phase 4〜7 のあいだ、検証されないまま保守されることになる。**
「後から挿入するコストが高いものだけ先に作る」（設計原則）の逆である。

**out-of-process にしても OS に対する境界にはならない。** Extension はユーザー権限の別プロセスであり、
manifest に何を書こうと `GetForegroundWindow` を呼べる。**実効的な防御は Core が
宣言外の facet を拒否すること**（Invariant 5）**であって、誰が OS を叩いたかではない。**
その拒否は Signal の送出元が Shell でも Extension でも同じように効く。
**したがって out-of-process を選んでも安全性は増えない。**

一方で Shell に置くことには実質がある。**Shell は Lumi が配布物として署名する唯一の
OS 特権コンポーネントであり、`hover.rs` にポーリング監視スレッドの実装と `windows_sys` 依存がすでにある。**
Phase 3 で新しく書くのは Win32 呼び出しと Signal 送出だけになる。

## Alternatives

### A. out-of-process のまま、`OS特権` の定義を書き直す（表を直す）

**利点**: Sensor が拡張点として残る。第三者が `sensor-calendar` / `sensor-music` を書ける道が
Phase 3 の時点で開く。world-state.md と extension.md を書き直さなくてよい。

**採らなかった理由**: 上記のとおり、**Phase 3 に Extension ホスト一式が乗る。**
拡張点が「開いている」ことに Phase 8 まで利用者がおらず、
**`Contracts` を変更する対価として得られるものが将来の可能性だけ**だった。
なお、この選択肢の内容（読み取り専用の OS 内観と特権操作を区別する）自体は正しい。
**Phase 9 で第三者 Extension を開くときに、そこで改めて決める。**

### B. Extension が Core 経由で Shell の `os.sensor.*` を呼ぶ

**利点**: OS を叩く場所が Shell 1箇所に残り、かつ Extension 機構も使う。表の読み方が最も素直。

**採らなかった理由**: **Shell の Win32 コードと Extension ホストと Tool 経路の3つ全部を作ることになる。**
A のコストに Shell 実装が足されるだけで、Sensor の中身は「Core に依頼して結果を Core に送り返す」
という往復になる。**払うものが最も多く、得るものが A と同じ**である。

### C. Phase 3 では Sensor を作らず、時刻と Lumi 自身の状態だけで自律する

**利点**: OS 読み取りが1つも要らない。

**採らなかった理由**: Phase 3 の完了条件は「1日つけっぱなしにして不快でない」であり、
**在席・idle・全画面（ゲーム中・会議中）を知らずにこれは通らない。**
「話しかけてよい瞬間か」の判定材料そのものを外すことになる。

## Trade-offs

**受け入れるコスト**

| | |
|---|---|
| **Sensor を足すには Shell のリリースが要る** | 第三者が Sensor を書けない。Phase 9 まで、新しい facet は Lumi 本体の変更である |
| **Shell が肥る** | 「OS 特権プリミティブのみ」という Shell の責務に、**周期的な観測**という新しい種類の仕事が入る。判断は持たないままだが、常駐スレッドは増える |
| **world-state.md §5 / extension.md / interfaces/extension.md の書き換え** | `sensor-desktop` はこれらの文書で out-of-process の代表例として使われている。**例を差し替える**必要がある |
| **Extension 機構が Phase 8 まで未検証のまま** | 設計としては存在するが、動くものが無い。**Phase 8 で初めて設計の誤りが見つかる**可能性を受け入れる |

**得るもの**

| | |
|---|---|
| **Phase 3 が Phase 3 の問題だけを扱う** | R5（自律の鬱陶しさ）が Phase 3 の唯一の難所であり、完了条件も体験ベースである。**プロセス生成と IPC のデバッグをそこに混ぜない** |
| **Confirmed 契約を変更しない** | authority-matrix はそのまま。Invariant にも触れない |
| **未検証の基盤を4 Phase ぶん抱えない** | 作った瞬間から Phase 8 まで、利用者1つで保守だけが発生する状態を避ける |

## Consequences

| どこ | 何が変わるか |
|---|---|
| [world-state.md](../architecture/world-state.md) §5 | 「Sensor Extension（out-of-process）」→「**Sensor**（Phase 3 は Shell、将来の外部 Sensor は Extension）」。facet の表・プライバシー規則・宣言の考え方は変えない |
| [extension.md](../architecture/extension.md) / [interfaces/extension.md](../interfaces/extension.md) | manifest の例から `lumi.sensor-desktop` を外し、**まだ存在しない例**（`sensor-calendar`）に差し替える。**実装済みの例のふりをさせない** |
| [event-model.md](../contracts/event-model.md) 例1 | 送出元が Sensor Ext → **Shell**。Signal 以降の経路は変わらない |
| [roadmap.md](../roadmap.md) Phase 3 | 「Sensor Extension — out-of-process」→「**Desktop Sensor（Shell）**」。未確定事項 14 を解消にする |
| [authority-matrix.md](../contracts/authority-matrix.md) | **✓ は1つも変えない。** 「Sensor Extension も例外ではない」という言い回しだけを「Sensor（Shell / Extension）」に直す。**表の意味は変わらず、item 14 を生んだ読み違いだけが消える** |
| [interfaces/shell.md](../interfaces/shell.md) | Shell → Core の Signal に `sensor.*` が加わる。**`stage.*` には出さない**（Stage は Core が配信した投影だけを見る） |
| Phase 9 | **「読み取り専用の OS 内観を第三者 Extension に許すか」がここで再び問題になる。** 本 ADR は先送りしただけで、答えていない |

**保証しないこと**: 本 ADR は「Shell に置いたから安全」とは言っていない。
**Shell はユーザー権限で動く同じ1台の中のプロセスであり、Sensor の追加は Lumi が読む OS 情報を増やす。**
守っているのは「**Core が受け取る facet の集合が固定であること**」だけで、
それは out-of-process でも同じだった。**この決定はセキュリティの決定ではなく、実装順序の決定である。**
