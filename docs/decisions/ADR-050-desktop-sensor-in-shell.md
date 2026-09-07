# ADR-050: デスクトップ Sensor は Shell に置き、out-of-process Sensor Extension を Phase 3 で作らない

| | |
|---|---|
| Status | Accepted |
| Date | 2026-09-06 |
| 関連 | [../roadmap.md](../roadmap.md) 未確定事項 14 / Phase 3, [../architecture/world-state.md](../architecture/world-state.md) §5, [../architecture/extension.md](../architecture/extension.md), [../interfaces/extension.md](../interfaces/extension.md), [../contracts/authority-matrix.md](../contracts/authority-matrix.md), [../contracts/event-model.md](../contracts/event-model.md), [../contracts/provenance.md](../contracts/provenance.md), [../contracts/privacy.md](../contracts/privacy.md), [../contracts/wire.json](../contracts/wire.json), [../contracts/security-boundaries.md](../contracts/security-boundaries.md), [ADR-017](ADR-017-out-of-process-tool-contract.md) |
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
| ★ **送る周期** | **TTL の半分以下**。変化が無くても送る（**変化時だけ送ると facet が期限切れ、Gate が閉じる**。→ [world-state.md](../architecture/world-state.md) §3） |
| ★ **`sensor.*` の trust** | **`ProvenanceClass.UNTRUSTED` / `TrustLevel.TAINTED`。** 送出元が Shell（信頼されたコンポーネント）でも、**運んでいるのは外界の観測**である（下記） |
| ★ **同意** | **明示的な許可を得るまで起動しない**（opt-in。同意は永続化する。下記） |
| ★ **新しい境界** | **Shell → Core は B8 として security-boundaries に足す。** B3（Core → Shell）の逆向きで、**信頼の低い側は Shell** である（下記） |

### ★ Shell → Core は新しい信頼の向きである

**B3 は「Core を信用しない Shell」しか定義していない。** Sensor を Shell に置くと
**Shell 発の Signal が Core に届く**ようになり、**Core が Shell を信用しない**向きが生まれる。
**その向きが表に無いと、B3 の表から出発した安全性レビューは Core 側の検証を確認しない。**

→ **[security-boundaries.md](../contracts/security-boundaries.md) に B8 を足す**
（信頼の低い側 = Shell と、Shell が読んだ OS の値。認可 = **Core 側の許可 key 集合**。
検証 = schema + payload を tainted 扱い）。
**保証しないことも書く**——侵害された Shell が「許可された key に偽の値を入れる」ことは防げない。

### ★ `user.activity_class` は Shell が決めない

`idle` / `browsing` / `focused_work` / `meeting` / `gaming` / `media` は**観測ではなく分類**であり、
**`AutonomyGate` が「割り込んでよいか」を直接これで決める**（[autonomy.md](../architecture/autonomy.md) §4）。
Shell に判断を持たせないという本 ADR 自身の規則に反するので、**Core が
`sensor.*` Signal ハンドラの中で決定論的に導出する。** Shell が送るのは材料だけである。

> ハンドラの中で導出するのは、[authority-matrix.md](../contracts/authority-matrix.md) の
> 静的検査 #10（`WorldFacet` の書き込みは Signal ハンドラ以外に存在しない）を満たすためでもある。
>
> **導出 facet の TTL は入力より長くできない**（`ttl(derived) = min(残り TTL of 入力)`）。
> 固定値を与えると、**根拠が全部切れた後も分類だけが生き残り、Gate がそれを見て割り込む**
> → [world-state.md](../architecture/world-state.md) §3。

### ★ `sensor.*` の Signal は tainted である

**送出元の信頼度と、運ばれてきた値の信頼度は別物である。**
`Signal.trust_level` は送出元から決まる（[event-model.md](../contracts/event-model.md)）ので、
**Shell を信頼したことが `user.focus_app` の中身を信頼することになってはならない。**
アプリ名は**その辺のアプリが自分で名乗った文字列**であり、World projection を通ってプロンプトに入る。
攻撃者が実行ファイル名や表示名を選べる以上、これは外部由来のテキストである（Invariant 3）。

**[provenance.md](../contracts/provenance.md) の「Sensor Extension の Signal = `UNTRUSTED`」を
`Sensor（Shell / Extension）の Signal` に読み替える。** 実装形態を変えても汚染は落ちない（Invariant 7）。

> **2つの enum を混ぜない。** あの表は **`ProvenanceClass`** の表であり、
> Policy が読む **`TrustLevel` には `UNTRUSTED` という値は無い**（`TRUSTED` / `TAINTED` の2つだけ）。
> 対応は既存の `taint()` が持つ——`taint(UNTRUSTED) == TAINTED`。**新しい規則を足していない。**

### ★ 同意を落とさない

Extension だったときは、[extension.md](../architecture/extension.md) §6 の
**`consent` を通らない限り `ready` にならなかった。** Shell に移すと、その門が黙って消える。

**消さない。同じ強さで残す。**

| Extension だったときの `consent` | Shell に移した後 |
|---|---|
| capability を提示して**同意を得る** | **何を観測するかを提示して、明示的な許可を得る** |
| **同意前は `load` しない** | **許可前は Sensor のスレッドを起動しない**（「送らない」ではなく「観測しない」） |
| 同意結果を `extensions.granted_permissions_json` に永続化 | **設定に永続化する。毎回聞かない** |
| manifest が変われば再同意 | **観測する key が増えたら再同意** |

**開示だけでは足りない。** 「知らせたうえで既定オン」は、
extension.md §6 が拒んだ **manifest がそのまま granted になる**形と同じである。
**答えていないことを同意と読まない**——アンインストーラで既に採った規則である
（[../roadmap.md](../roadmap.md) 2g「サイレントアンインストールでは消さない」）。

**断れる。** 許可しなければ Desktop Sensor は動かず、Lumi は在席も前面アプリも知らないまま
`Unknown` で走る（§3）。**自律発話の質は落ちるが、Phase 3 は成立する**——
`AutonomyGate` は不明な facet を**通さない**側に倒れる（fail-closed）。

永続化される先（`world:*` の DomainEvent、既定 30 日）は
[privacy.md](../contracts/privacy.md) §2 の行 5 が既に覆っているので、
新しい保存先は増えない。

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

**Phase 3 の時点で Extension ホストが1行も無い。** `core/lumi/extensions/` は存在しない。
（**`Signal` の型そのものは既にある**——`core/lumi/kernel/event.py` に凍結 dataclass として定義され、
`stream_key` / `sequence_id` を持たないことの検査も `core/tests/test_kernel_event.py` にある。
**足りないのは型ではなく、届ける経路とハンドラである。**）

`sensor-desktop` を out-of-process にすると、Phase 3 は
**「プロセス生成・IPC・manifest 解析・capability 検査」を新規に作り、その上で最初の利用者が
foreground app 名を数秒ごとに送るだけのプロセス**になる。
**Shell に置けば、要るのは Win32 呼び出しと Signal の inbound 経路だけ**であり、
**その inbound 経路はどちらを選んでも要る**（Extension も同じ経路で Signal を送る）。

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
| [security-boundaries.md](../contracts/security-boundaries.md) | **B8（Shell → Core）を追加する。** B3 の逆向きで、これまで表に無かった信頼の向きである |
| [interfaces/shell.md](../interfaces/shell.md) | Shell → Core の Signal に `sensor.*` が加わる。**`stage.*` には出さない**（Stage は Core が配信した投影だけを見る） |
| [provenance.md](../contracts/provenance.md) | 「Sensor Extension の Signal」→「**Sensor（Shell / Extension）の Signal**」。`ProvenanceClass.UNTRUSTED`（→ `TrustLevel.TAINTED`）であることは変えない。**送出元の信頼度が payload の信頼度にならないことを明記する** |
| [world-state.md](../architecture/world-state.md) §3 | `user.activity_class` の source が Desktop Sensor → **Core（`sensor.*` ハンドラで導出）**。`time.*` は**facet をやめて導出値にする**（時計は陳腐化せず、静的検査 #10 の例外を作る必要も無くなる） |
| [autonomy.md](../architecture/autonomy.md) | `world.get("time.quiet_hours")` → 時計から直接引く。facet ゲートは「既知であること」を条件に含む（`Unknown` を通さない） |
| **Sensor の送出周期** | **TTL の半分以下でハートビートを送る**。**変化時だけ送る実装にすると、変わらない限り facet が期限切れ、`activity_class` も `Unknown` になり、自律発話が静かに止まる** |
| manifest の `ttl_ms` | **権威ではなく上限のヒント。** Core は自分の値と短い方を採る（**Extension が観測を Core の意図より長生きさせられない**） |
| Phase 3a | **Shell → Core の `sensor.*` の封筒・名前空間・schema を [wire.json](../contracts/wire.json) と [interfaces/shell.md](../interfaces/shell.md) に定義する。** 現在 `os.*` は Core → Shell の一方向しか無く、**Shell 発の inbound が存在しない**。実装前にここを埋める |
| Phase 9 | **「読み取り専用の OS 内観を第三者 Extension に許すか」がここで再び問題になる。** 本 ADR は先送りしただけで、答えていない |

**保証しないこと**: 本 ADR は「Shell に置いたから安全」とは言っていない。
**Shell はユーザー権限で動く同じ1台の中のプロセスであり、Sensor の追加は Lumi が読む OS 情報を増やす。**
守っているのは「**Core が受け取る facet の集合が固定であること**」だけで、
それは out-of-process でも同じだった。**この決定はセキュリティの決定ではなく、実装順序の決定である。**
