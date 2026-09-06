# ADR-050: デスクトップ Sensor は Shell に置き、out-of-process Sensor Extension を Phase 3 で作らない

| | |
|---|---|
| Status | Accepted |
| Date | 2026-09-06 |
| 関連 | [../roadmap.md](../roadmap.md) 未確定事項 14 / Phase 3, [../architecture/world-state.md](../architecture/world-state.md) §5, [../architecture/extension.md](../architecture/extension.md), [../interfaces/extension.md](../interfaces/extension.md), [../contracts/authority-matrix.md](../contracts/authority-matrix.md), [../contracts/event-model.md](../contracts/event-model.md), [../contracts/provenance.md](../contracts/provenance.md), [../contracts/privacy.md](../contracts/privacy.md), [../contracts/wire.json](../contracts/wire.json), [ADR-017](ADR-017-out-of-process-tool-contract.md) |
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
| ★ **送るのは生の観測だけ** | 前面アプリ名 / idle 秒 / 在席 / 全画面 / 音声再生 / CPU / VRAM。**`user.activity_class` は Shell が決めない**（下記） |
| ★ **`sensor.*` の trust** | **`UNTRUSTED`。** 送出元が Shell（信頼されたコンポーネント）でも、**運んでいるのは外界の観測**である（下記） |
| ★ **同意** | **初回に開示し、設定で無効にできる。無効なら Sensor を起動しない**（下記） |

### ★ `user.activity_class` は Shell が決めない

`idle` / `browsing` / `focused_work` / `meeting` / `gaming` / `media` は**観測ではなく分類**であり、
**`AutonomyGate` が「割り込んでよいか」を直接これで決める**（[autonomy.md](../architecture/autonomy.md) §4）。
Shell に判断を持たせないという本 ADR 自身の規則に反するので、**Core が
`sensor.*` Signal ハンドラの中で決定論的に導出する。** Shell が送るのは材料だけである。

> ハンドラの中で導出するのは、[authority-matrix.md](../contracts/authority-matrix.md) の
> 静的検査 #10（`WorldFacet` の書き込みは Signal ハンドラ以外に存在しない）を満たすためでもある。

### ★ `sensor.*` の Signal は `UNTRUSTED` である

**送出元の信頼度と、運ばれてきた値の信頼度は別物である。**
`Signal.trust_level` は送出元から決まる（[event-model.md](../contracts/event-model.md)）ので、
**Shell を信頼したことが `user.focus_app` の中身を信頼することになってはならない。**
アプリ名は**その辺のアプリが自分で名乗った文字列**であり、World projection を通ってプロンプトに入る。
攻撃者が実行ファイル名や表示名を選べる以上、これは外部由来のテキストである（Invariant 3）。

**[provenance.md](../contracts/provenance.md) の「Sensor Extension の Signal = `UNTRUSTED`」を
`Sensor（Shell / Extension）の Signal` に読み替える。** 実装形態を変えても汚染は落ちない（Invariant 7）。

### ★ 同意を落とさない

Extension だったときは、[extension.md](../architecture/extension.md) §6 の
**`consent` を通らない限り `ready` にならなかった。** Shell に移すと、その門が黙って消える。

**消さない。** 初回起動時に「何を観測するか」を開示し、設定で無効にできるようにし、
**無効なら Desktop Sensor のスレッドを起動しない。** 永続化される先（`world:*` の DomainEvent、
既定 30 日）は [privacy.md](../contracts/privacy.md) §2 の行 5 が既に覆っているので、
足りないのは**ユーザーが知り、止められること**だけである。

> **AIRI に `permissionResolver` が無いことを批判した**（extension.md §6）その同じ機構を、
> 自分の Sensor では飛ばす——という形にしない。

**out-of-process Capability Extension の機構そのものは捨てない。** 最初にそれを本当に必要とするのは
**Phase 4b の Browser Extension**（Playwright / Class B → [ADR-017](ADR-017-out-of-process-tool-contract.md)）であり、
**そこで作る。** manifest 署名・信頼レベル・第三者向けインストール UI は Phase 9 のままである。

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

**そして次の利用者は Phase 4b の Browser Extension である**（Playwright / Class B。
Class B が out-of-process であることは [ADR-017](ADR-017-out-of-process-tool-contract.md) が決めている）。
つまり Extension ホストは**どのみち 4b で作る。**
問題は「作るか」ではなく「**1つ前の Phase で、Shell が数行で出せる観測のために先に作るか**」である。

**先に作れば、Phase 3 のあいだ利用者1つで保守だけが発生する。**
4b で作れば、**最初の利用者は本当に out-of-process でなければならないもの**になる。
「後から挿入するコストが高いものだけ先に作る」（設計原則）に照らすと、
Extension ホストは**後から挿入しても高くない**——4b が来たときに 4b のために作ればよい。

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
拡張点が「開いている」ことに **Phase 4b まで**利用者がおらず、
**`Contracts` を変更する対価として得られるものが1 Phase ぶんの前倒しだけ**だった。
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
| **Extension 機構が Phase 4b まで未検証のまま** | 設計としては存在するが、動くものが無い。**4b で初めて設計の誤りが見つかる**可能性を受け入れる。Sensor で先に一度動かしておけば 4b が楽になった、という筋はある |
| **Core が `user.activity_class` を導出する責任を持つ** | 分類ロジックが Core に入る。**決定論的コードで書く**（LLM に決めさせない） |

**得るもの**

| | |
|---|---|
| **Phase 3 が Phase 3 の問題だけを扱う** | R5（自律の鬱陶しさ）が Phase 3 の唯一の難所であり、完了条件も体験ベースである。**プロセス生成と IPC のデバッグをそこに混ぜない** |
| **Confirmed 契約を変更しない** | authority-matrix はそのまま。Invariant にも触れない |
| **未検証の基盤を1 Phase 早く抱えない** | Phase 3 のあいだ、利用者1つで保守だけが発生する状態を避ける |
| **最初の Extension が「本当に out-of-process でなければならないもの」になる** | Playwright は in-core にできない。**機構の必要性がその時点で自明である** |

## Consequences

| どこ | 何が変わるか |
|---|---|
| [world-state.md](../architecture/world-state.md) §5 | 「Sensor Extension（out-of-process）」→「**Sensor**（Phase 3 は Shell、将来の外部 Sensor は Extension）」。facet の表・プライバシー規則・宣言の考え方は変えない |
| [extension.md](../architecture/extension.md) / [interfaces/extension.md](../interfaces/extension.md) | manifest の例から `lumi.sensor-desktop` を外し、**まだ存在しない例**（`sensor-calendar`）に差し替える。**実装済みの例のふりをさせない** |
| [event-model.md](../contracts/event-model.md) 例1 | 送出元が Sensor Ext → **Shell**。Signal 以降の経路は変わらない |
| [roadmap.md](../roadmap.md) Phase 3 | 「Sensor Extension — out-of-process」→「**Desktop Sensor（Shell）**」。未確定事項 14 を解消にする |
| [authority-matrix.md](../contracts/authority-matrix.md) | **✓ は1つも変えない。** 「Sensor Extension も例外ではない」という言い回しだけを「Sensor（Shell / Extension）」に直す。**表の意味は変わらず、item 14 を生んだ読み違いだけが消える** |
| [interfaces/shell.md](../interfaces/shell.md) | Shell → Core の Signal に `sensor.*` が加わる。**`stage.*` には出さない**（Stage は Core が配信した投影だけを見る） |
| [provenance.md](../contracts/provenance.md) | 「Sensor Extension の Signal」→「**Sensor（Shell / Extension）の Signal**」。`UNTRUSTED` であることは変えない。**送出元の信頼度が payload の信頼度にならないことを明記する** |
| [world-state.md](../architecture/world-state.md) §3 | `user.activity_class` の source が Desktop Sensor → **Core（`sensor.*` ハンドラで導出）**。`time.*` は**facet をやめて導出値にする**（時計は陳腐化せず、静的検査 #10 の例外を作る必要も無くなる） |
| [autonomy.md](../architecture/autonomy.md) | `world.get("time.quiet_hours")` → 時計から直接引く |
| manifest の `ttl_ms` | **権威ではなく上限のヒント。** Core は自分の値と短い方を採る（**Extension が観測を Core の意図より長生きさせられない**） |
| Phase 3a | **Shell → Core の `sensor.*` の封筒・名前空間・schema を [wire.json](../contracts/wire.json) と [interfaces/shell.md](../interfaces/shell.md) に定義する。** 現在 `os.*` は Core → Shell の一方向しか無く、**Shell 発の inbound が存在しない**。実装前にここを埋める |
| Phase 9 | **「読み取り専用の OS 内観を第三者 Extension に許すか」がここで再び問題になる。** 本 ADR は先送りしただけで、答えていない |

**保証しないこと**: 本 ADR は「Shell に置いたから安全」とは言っていない。
**Shell はユーザー権限で動く同じ1台の中のプロセスであり、Sensor の追加は Lumi が読む OS 情報を増やす。**
守っているのは「**Core が受け取る facet の集合が固定であること**」だけで、
それは out-of-process でも同じだった。**この決定はセキュリティの決定ではなく、実装順序の決定である。**
