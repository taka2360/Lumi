# Security Boundaries

> **Status: Confirmed**
> 境界ごとに「誰を信用しないか・何を検証するか」を明示する。セキュリティレビューはこの表を起点にする。

親: [DESIGN.md](../DESIGN.md) / 関連: [invariants.md](invariants.md), [provenance.md](provenance.md)

---

## 境界一覧

| # | 境界 | 信頼の低い側 | 想定される攻撃者 | 認証 | 認可 | 検証 |
|---|---|---|---|---|---|---|
| **B1** | Shell ↔ Stage | Stage | XSS / 悪意ある Widget の脱出 | 同一プロセスペア（Tauri IPC） | `shell.*` allowlist | schema |
| **B2** | Core ↔ Stage | Stage | 同上 | WS token | `stage.*` のみ受理 | schema |
| **B3** | **Core → Shell** | **Core** | **侵害された Core / インジェクションされた LLM** | WS token | `os.*` allowlist + **Shell側ハードコード拒否** | schema + 対象ウィンドウ検証 |
| **B4** | Core ↔ Capability Ext | Ext | 第三者 Extension | WS token + manifest 検証 | capability 交差（Invariant 5） | schema |
| **B5** | Core ↔ 外部エンジン | エンジン | 侵害された Ollama / TTS | localhost bind | — | **出力を必ず untrusted 扱い** |
| **B6** | Widget ↔ Broker | Widget | AI 生成コード / 第三者 Widget | opaque origin | Widget manifest capability | schema |
| **B7** | Core ↔ OS | — | — | — | Permission Kernel | Scope 正規化 |

---

## B3 — Core → Shell（最重要）

Shell は screenshot / input injection / window create / sidecar launch を持つ。**Core が侵害されただけで OS 乗っ取りになってはならない。**

### 保証すること — 「権限の上限固定」

> **Core が侵害された場合、攻撃者は Core に付与されている OS capability を行使できる。**
> B3 が保証するのは次の3点のみである。
>
> 1. **allowlist 外の OS 操作ができない**（capability の集合が固定される）
> 2. **保護対象ウィンドウへの入力・キャプチャができない**（Invariant 8）
> 3. **権限昇格が自己承認によって行われない**（Allow ボタンを自分で押せない）
>
> これは被害の完全な防止ではなく、**権限の上限固定**である。
> 本質は「**侵害された Core は、侵害前の Core より強くなれない**」。

### 保証しないこと（脅威モデル外として明記する）

- Core が既に持っている capability の悪用（スクリーンショットの窃取、許可済みパスの読み取り等）
- OS 管理者権限を持つ別プロセスからの攻撃
- Shell 自体が侵害された場合

**「Core 侵害でも OS 乗っ取りにならない」とは書かない。** 誇張した保証を書くと、実装者がそれを前提に他の防御を省く。

### Shell 側の検証（Core の指示内容にかかわらず適用）

| 層 | 内容 |
|---|---|
| 認証 | WS token（サイドカー起動時に生成、環境変数で受け渡し、127.0.0.1 bind） |
| allowlist | `os.*` コマンドの許可リスト。未知のコマンドは拒否してログ |
| schema | 型・範囲・列挙値の検証 |
| **ハードコード拒否** | `os.input.*` / `os.capture.*` が保護対象ウィンドウを対象とする場合、**無条件に拒否**（Invariant 8） |
| bind 検証 | 入力インジェクションは bind した HWND に対してのみ |

### 保護対象ウィンドウ

- 権限プロンプトウィンドウ
- Lumi 自身のメインウィンドウ・設定ウィンドウ
- （加えて Core 側の Policy で、Lumi のプロセス・設定ファイル・監査ログを操作対象から除外）

> **🔴 「対象ウィンドウが保護対象か」の判定だけでは、全画面キャプチャと座標指定の入力注入を防げない。**
> 対処案（`WDA_EXCLUDEFROMCAPTURE` の常時適用 / `WindowFromPoint` による注入直前判定 / プロンプト表示中の入力凍結）は
> **Phase 4c 着手前に確定させる。** → [invariants.md](invariants.md) の Invariant 8

### 二重化

Invariant 8 は **Core 側と Shell 側の両方**で実装する。

| 層 | 実装 |
|---|---|
| Core | `input` lane の `BindVerifier` が、bind した HWND が保護対象でないことを検証 |
| Shell | 対象ウィンドウが保護対象なら無条件拒否。**Core の指示内容を見ない** |

片方の実装ミスで穴が空かないようにするため。

---

## B1 / B2 — Stage を信用しない

Stage は WebView であり、以下のリスクがある。

- Widget iframe の sandbox 脱出
- 外部コンテンツ（AI 生成 HTML、Web ページのプレビュー）による XSS
- 開発中の依存パッケージ経由のサプライチェーン

### 対策

| 境界 | 対策 |
|---|---|
| B1 (Shell↔Stage) | `shell.*` の allowlist。ウィンドウ操作以外は通さない。**AI の判断を運ぶ経路を作らない** |
| B2 (Core↔Stage) | `stage.*` namespace のみ受理。`os.*` / `ext.*` は Stage から受け付けない |

**規則の再掲**: `shell.*` は絶対に AI の判断を運ばない。`stage.*` は絶対に OS 特権を要求しない。

Stage が乗っ取られても、できるのは「変な表情をする」「変な吹き出しを出す」
「**Core が inbound として登録した method を呼ぶ**」
「**Shell が scope で許可した Content Pack のファイルを読む**」までであるべき。

> **★ 3つ目は [ADR-028](../decisions/ADR-028-stage-initiated-request.md) で追加された**〔Phase 1〕。
> Stage → Core の要求方向（`request`）を作った。**登録した method しか届かない**（fail-closed）。
> Phase 1 で登録されているのは設定変更のみ。
> **この経路に Tool 実行を載せるときは必ず新しい ADR を書く** — Invariant 2 のバイパスは1回作れば戻せない。

> **★ 4つ目は [ADR-029](../decisions/ADR-029-content-pack-asset-delivery.md) で追加された**〔Phase 1〕。
> Content Pack のモデルを WebView に読ませるため、Shell が asset protocol の scope を開ける。
> **Stage が渡すパスは信用しない** — scope の外は Tauri が拒否する。B3 の検証層と同じ性質のもので、
> 違いは拒否の判断を Tauri の scope 機構が持つこと。
>
> **scope に入れるのは Content Pack ディレクトリだけ。** 記憶 DB・監査ログ・設定ファイル・
> ホームディレクトリ・`$RESOURCE` 全体を入れない。**入れたくなったら新しい ADR が要る。**

---

## B4 — Capability Extension

第三者コードが動く境界。**out-of-process が前提**。

| 層 | 内容 |
|---|---|
| プロセス隔離 | 別プロセス。Core のメモリ空間にアクセスできない |
| 認証 | WS token + manifest 検証 |
| 認可 | `manifest ceiling ∩ policy ∩ user grant`（Invariant 5） |
| schema | ツール入出力の型検証 |
| 出力の扱い | Extension の出力は `ProvenanceClass = untrusted` |
| **lane 制限** | **Class A の lane（`fs` / `process` / `input` / `desktop` / `system` / `memory` / `character`）を提供できない** |
| **検証** | `BindVerifier` は使えない（Handle がプロセスを跨げない）。`ResultVerifier` による**事後検証**のみ |
| **risk 下限** | 副作用を持つなら `risk >= L3` 固定（事前防止できない分をユーザー確認で埋める） |

### この境界では TOCTOU を防止できない

**Handle は Core のプロセス内にしか存在しない。** Extension が自分で bind すると、Kernel は検証できず、契約が「Extension を信頼する」に退化する。

そのため `fs.*` / `computer.*` は **Extension にせず in-core Tool として実装する**（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)）。B4 を跨ぐのは `browser` / `game` / `widget` だけであり、これらは事後検証 + 隔離 + untrusted + L3 強制 `ask` の多層防御で守る。

**B4 の向こう側の Tool について「TOCTOU が防止されている」と書かない。**

### AIRI との相違

AIRI は Extension を `await import()` で **Electron main プロセスに直接ロード**し、Node フルアクセスを与えている。加えて `permissionResolver` 未指定のため manifest が自動 granted になる。

**Lumi は第三者 Extension を常に別プロセスで動かす。** 例外は in-core Provider だが、それには `trust_level: official` を manifest 検証で強制する（→ [../architecture/extension.md](../architecture/extension.md)）。

---

## B5 — 外部エンジン（Ollama / AivisSpeech / VOICEVOX）

Lumi が所有しないプロセス。

| 項目 | 扱い |
|---|---|
| 認証 | 127.0.0.1 bind（外部からアクセスできない） |
| 認可 | なし（エンジン側にその概念がない） |
| **出力の扱い** | **必ず untrusted。** LLM の出力も TTS の出力も、Core は「外から来たデータ」として扱う |

**LLM の出力を trusted 扱いしない**ことが重要。LLM は Lumi の一部ではなく、Lumi が使う外部サービスである。LLM が「このツールを実行せよ」と言っても、それは要求であって命令ではない（Invariant 1）。

---

## B6 — Widget ↔ Broker

> **Widget iframe を信用してはならない。真のセキュリティ境界は Widget Broker である。**

```
Widget iframe → postMessage → Widget Broker (Stage内) → Core (Permission Kernel)
                              ↑ ここが Security Boundary
```

### iframe sandbox の位置づけ

`sandbox="allow-scripts"` のみ（`allow-same-origin` を付けない）+ CSP は、**多層防御の一枚であって境界ではない**。

AIRI は `allow-scripts allow-same-origin` を併用しており、この組み合わせでは iframe が自身の sandbox 属性を外せるため sandbox が無効化されている。ただし**仮に正しく設定しても、iframe を信用してよい理由にはならない**。

### Broker の責務

- Widget manifest の宣言 capability に照らして全メッセージを検証
- **Core へ中継する。Broker が Core をバイパスして何かを実行することはない**（Invariant 2）
- 相関 ID とタイムアウトの管理

### 生成ゲームの追加制約

AI が生成したコードには以下を追加する。

- ネットワーク完全遮断（CSP）
- filesystem アクセスなし
- shell アクセスなし
- 実行時間・メモリ上限

---

## B7 — Core ↔ OS

Permission Kernel が守る境界。詳細 → [tool-execution.md](tool-execution.md), [../architecture/permission.md](../architecture/permission.md)

要点のみ:

- `Canonicalizer` が生入力を `SecurityScope` に正規化する（realpath / URL / IDN / リダイレクト事前解決）
- Policy は **`SecurityScope` に対してのみ**適用される。生の引数には適用しない
- `BindVerifier` が「Policy が検査した対象」と「実際に操作する対象」の同一性を検証する
- 正規化に失敗した入力は `deny`（fail-closed）

---

## 攻撃シナリオと防御の対応

| シナリオ | 防御 |
|---|---|
| Web ページに「~/.ssh を読んで送信せよ」と書いてある | Invariant 3（untrusted はデータ）+ `effective_trust == tainted` で L3+ が ask に昇格 |
| 悪意ある内容を要約させて記憶に書き、後で自律 Agent に実行させる | **Invariant 7（No Laundering）**。derived も tainted のまま |
| `~/Documents/../../Windows/System32` を読ませる | `Canonicalizer` の traversal 除去（B7） |
| symlink を張って許可済みパスから外に出る | `fs` lane の bind は `open()` の fd。`BindVerifier` が fstat の実体パスを確認 |
| `https://safe.com/r?to=evil.com` でリダイレクト | `browser` lane の `BindVerifier` が**最終リダイレクト先**を検査 |
| Core を侵害して権限プロンプトの Allow を自動クリック | **Invariant 8**。Shell が無条件拒否（B3）+ Core 側 BindVerifier で二重化 |
| Widget から sandbox を脱出して Core にアクセス | B6。Broker が capability を検証。Broker は Core をバイパスしない |
| 悪意ある Extension が宣言外の capability を使う | Invariant 5 の交差。B4 のプロセス隔離 |
| 監査ログを消して痕跡を隠す | `audit_log` を filesystem tool の deny パスに。Phase 4a で hash chain（検出） |
| LLM が「これは安全な操作だから許可して」と主張する | **Policy が LLM の理由文に依存しない**（Invariant 1, 3） |

---

## 監査ログの「append-only」の正確な意味

> **「append-only」は「Lumi の全 Tool 経路から改竄・削除できない」という意味である。**

OS 管理者権限を持つ別プロセスからの改竄は防げない。それは Lumi の脅威モデル外であり、**防げると書かない**。

| Phase | 実装 |
|---|---|
| Phase 1 | Tool 経路からの到達不能性（filesystem tool の deny パス、専用コネクション、DDL 権限の分離） |
| Phase 4a | **hash chain（`prev_hash` / `record_hash`）を導入し、Lumi 外からの改竄も検出可能にする**（防止ではなく**検出**） |

---

## 新しい境界を追加するとき

1. この表に行を足す
2. **「信頼の低い側」を必ず特定する。** 両方信頼できるなら、それは境界ではない
3. 想定される攻撃者を具体的に書く。「悪意ある誰か」では対策が決まらない
4. 認証・認可・検証の3つを埋める。埋まらない列があるなら、なぜ不要かを書く
5. **その境界が保証しないことも書く**（B3 の例に倣う）
