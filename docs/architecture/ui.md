# UI Architecture — Shell, Stage, Character, Widget

親: [DESIGN.md](../DESIGN.md) / 関連: [../interfaces/shell.md](../interfaces/shell.md), [../interfaces/renderer.md](../interfaces/renderer.md), [../contracts/security-boundaries.md](../contracts/security-boundaries.md)

> **このファイルが唯一の定義場所であるもの**: Shell / Stage の責務表、ウィンドウ一覧、Tauri 2 固有の課題、AIRI から借りる運用知見、表情の合成規則（Mood + ACT）。

---

## 1. Shell（Tauri 2 / Rust）

**OS 特権プリミティブのみ。判断を持たない**（Invariant 8 の拒否を除く）。

| 責務 | 内容 |
|---|---|
| ウィンドウ | 透過 / 常時最前面 / クリックスルー / ヒットテスト / 位置永続化 |
| 入力 | グローバルホットキー / **カーソル監視（ホバー検知）** |
| キャプチャ | スクリーンショット |
| インジェクション | マウス / キーボード |
| プロセス | Core サイドカーの起動・生存監視・確実な終了 |
| トレイ | メニュー / 表示切替 |
| **検証** | `os.*` の認証 / schema / allowlist / **保護対象への無条件拒否**（B3） |

### モジュール構成

```
shell/src-tauri/src/
├── lib.rs           起動シーケンスのみ。token を作り、builder を組み、setup と RunEvent を回す
├── window.rs        ウィンドウ契約（label / 保護対象 / PanelKind）と**純粋関数**（配置・寸法）
├── window_open.rs   実ウィンドウの生成と前面化。それを呼ぶ `shell.*` コマンドも同居する
├── content_pack.rs  **起動する Core の** Content Pack だけを asset scope に許可する（ADR-029）
├── app.rs           **ウィンドウにも Core にも依存しない** app-level command のみ（終了 / 外部リンク）
├── tray.rs          トレイのメニューと、ネイティブ表示名への locale 反映
├── hover.rs         カーソル監視。クリックスルーとホバー状態の判定（純粋関数）
├── core_endpoint.rs 要求元ウィンドウの label でトークンと接続先を選ぶ（ADR-042）
├── core_process.rs  Core の起動・生存監視・確実な終了
├── os_command.rs    `os.*` の**検証**（純粋。B3 の allowlist / schema / 保護対象）
├── os_exec.rs       **検証済みの `OsCommand` だけ**を実行する
├── ws_client.rs     Core との WS。フレームの parse / 送受 / result の組み立て
├── job_object.rs    Windows Job Object（force-kill でもゾンビを残さない層）
├── locale.rs        locale の解決（純粋）
└── wire_contract.rs `docs/contracts/wire.json` との突き合わせ（**test のみ**。配布物に入らない）
```

**`os_command` → `os_exec` → `ws_client` の3ファイルが B3 の3段そのもの**であり、
「検証してから実行する」がファイル境界として読める。

`app.rs` に置いてよいのは**ウィンドウにも Core にも依存しない**コマンドだけ。
ウィンドウを開くものは `window_open.rs`、トレイに紐づくものは `tray.rs` に置く。
**この条件を書いていなかったために `tray.rs` が「その他コマンド」置き場になった**という経緯がある。

### ウィンドウ一覧

| ウィンドウ | 用途 | 特性 |
|---|---|---|
| `stage` | キャラクター本体 | 透過 / 最前面 / クリックスルー / 非フォーカス |
| ~~`bubble`~~ | 吹き出し | **Phase 1 では作らない。`stage` の中に描く**（下記） |
| `permission` | **権限プロンプト** | **フォーカス必須 / Invariant 8 の保護対象** |
| `credits` | クレジットとライセンス（トレイ / `stage` の操作メニュー → クレジット） | 通常ウィンドウ。**Core に接続しない**（内容は静的） → [../licensing.md](../licensing.md) §6 |
| `help` | **使いかた**（トレイ / 操作パレット → 使いかた）〔[ADR-047](../decisions/ADR-047-character-surface-interactions.md)〕 | 通常ウィンドウ。**Core に接続しない**（下記） |
| `settings` | 設定 | 通常ウィンドウ。**`panel` role で Core に接続する**〔2g〕 |
| `inspector` | 開発用 | 同上〔2g〕。§5 |
| `memory` | **記憶の閲覧・編集・削除・エクスポート・全消去** | 同上〔2g〕。§5b |
| `widget` | Widget / Gamelet | 通常ウィンドウ（Phase 7） |

`credits` / `help` 以外の3つは **`panel` role**（namespace `panel.`）で繋ぐ
→ [../decisions/ADR-042-panel-windows-and-panel-role.md](../decisions/ADR-042-panel-windows-and-panel-role.md)。

| `panel` の規則 | 内容 |
|---|---|
| 接続数 | **複数を許す唯一の role。** `shell` / `stage` は各1本のまま |
| Core → panel | **`notify` のファンアウトのみ。** 宛先0でも成功（誰も開いていないだけ） |
| **`invoke`** | **禁止。** 答えを待つコマンドは宛先が一意でなければ成立しない |
| token | **専用の3本目。** Shell は**要求元ウィンドウのラベル**でトークンを選ぶ |

### 初回セットアップ UI は `stage` ウィンドウの中に置く〔Phase 0〕

独立ウィンドウにしない。理由は **Core ↔ Stage の WS 接続を 1 本に保つため**。
ウィンドウを増やすと接続が増え、「どの Stage に送るか」という宛先の概念が
`stage.*` に必要になる（B2 の検証対象が増える）。Phase 0 で払う価値がない。

- キャラクターの隣にパネルとして出す。当たり判定領域はキャラクターとパネルの**和**を渡す
- クリックスルーはパネル上でも解除される（領域に含まれるため）
- 状態と進捗は Core が `stage.setup.state` で配信する。**Stage は表示するだけ**

→ [setup.md](setup.md)

### アプリ操作は `stage` ウィンドウからも到達できる〔Phase 1 / 2g・[ADR-047](../decisions/ADR-047-character-surface-interactions.md) で改訂〕

トレイを知らないユーザーにも基本操作が見つかるよう、`stage` ウィンドウからもアプリ操作へ
辿り着けるようにする。トレイの項目は代替経路として維持する。

**〔2g〕操作列は文字ではなくアイコン（シグニファイア）の横並びにする。**
キャラクターの上に文字のボタンが並ぶと、**ボタンの列がキャラクターより大きくなる。**
アイコンは常時見えていてよい大きさに収まり、意味は `aria-label` と `title` が持つ。

| アイコン | 開くもの |
|---|---|
| ⚙ | `settings` ウィンドウ |
| ◎ | `inspector` ウィンドウ |
| ✿ | `memory` ウィンドウ |
| ? | `help` ウィンドウ〔[ADR-047](../decisions/ADR-047-character-surface-interactions.md)〕 |
| ⓘ | `credits` ウィンドウ |
| ⏻ | 終了 |

- **アイコンだけのボタンには必ず `aria-label` を付ける。** 記号は i18n を通さない
  （記号に翻訳は無い）が、**読み上げられる名前は i18n を通す**
- **〔ADR-047〕操作列はキャラクターの右クリックで出す。ホバーでは出さない。**
  デスクトップに住んでいるものの上を通り過ぎただけで管理 UI が現れると、キャラクターが
  「操作対象のウィジェット」に見える。**通常時に見えているのはキャラクターとマイク表示だけ**
- パレットは OS のコンテキストメニューではなく `stage` ウィンドウ内の要素にする。
  Windows 標準のメニューはキャラクターより大きく、**出すとキャラクターが隠れる**。
  ネイティブメニューにすると項目のラベルが Shell 側の文字列になり、i18n も二重化する
- 出す位置は押された点。**ウィンドウの内側にクランプする**ので、端で押しても外へはみ出さない
- **開いている間だけ、ヒット領域をウィンドウ全面にする。** 領域外はクリックスルーなので、
  そうしないとパレット外のクリックが Stage に届かず「外側を押したら閉じる」が成立しない。
  閉じれば元の領域（キャラクター＋マイク）へ戻る
- 閉じる操作は「パレットの外を左クリック」「`Esc`」「`stage` ウィンドウがフォーカスを失う」。
  どの項目も自前のウィンドウを開くので、開いた先から戻ったときにパレットが残っていない。
  **失敗したときは何も開かずフォーカスも移らない**ので、エラー表示は画面に残る
- 開いたまま右クリックすると押した点へ移る。**押した瞬間に閉じない**——右ボタンは
  `pointerdown` と `contextmenu` の2イベントで届くので、前者で閉じると後者が開き直す
- ブラウザ既定のコンテキストメニューは `stage` ウィンドウ内では出さない
- **初回ヒントは未決。** 初回のみの操作ヒントや、セットアップ完了後の操作説明を後から
  足せるよう、パレットの状態は「開く位置」だけに留める

#### `help` は Core に繋がない〔[ADR-047](../decisions/ADR-047-character-surface-interactions.md)〕

**右クリックは見えない操作である。** その代償を払うのが `help` であり、
トレイの**先頭**と操作パレットの両方から開ける。

**`credits` と同じく静的ページにするが、理由は違う。** credits は配布物としての義務なので
Lumi の稼働状態に依存してはならない。`help` は、**説明が最も要る場面が `blocked` の画面
だから**である——セットアップが完了しておらずキャラクターが出ていないときに読めない操作説明は、
操作説明として成立しない。したがって `help` からも `core/*` を import しない。

内容は「マウス操作の表」「パレットの各項目」「見つからないときはトレイ」の3つ。
**グリフと項目名の対応は `stage/src/actions/items.ts` の1箇所**に置き、
ボタン自身と `help` の両方がそこを読む。2箇所に書くと、説明のグリフとボタンのグリフがずれる。
- [クレジットとライセンス] は既存の静的な `credits` ウィンドウを開く。同じウィンドウが既にあれば前面へ出す
- Stage の IPC からウィンドウを生成するときは、Tauri の**非同期コマンド**として実行する。同期 IPC ハンドラ内で
  `WebviewWindowBuilder::build()` を呼ぶと、Windows ではメインイベントループへの生成依頼と循環待ちになり、
  クレジットが白画面のまま終了操作まで停止する
- [終了] はトレイおよびセットアップ画面と同じ終了経路を使い、Core サイドカーも確実に終了する
- どちらも AI 判断や Core 状態を運ばない `shell.*` 操作であり、Stage のストアには状態を追加しない

### 吹き出しも `stage` ウィンドウの中に置く〔Phase 1〕

**初回セットアップ UI とまったく同じ理由**（WS 接続を1本に保つ）。加えて、吹き出しは
**キャラクターとの相対位置がすべて**であり、別ウィンドウにすると Stage が知らない座標系
（画面座標・DPI・ウィンドウ位置）を跨いで追従させることになる。

代償: 吹き出しは `stage` ウィンドウの内側にしか出せない。長い文はウィンドウ幅で折り返す。
**別ウィンドウ化が必要になったら、そのときに独立させる**（Phase 7 の Widget と同じ判断）。

| 規則 | 内容 |
|---|---|
| 出す内容 | **いま喋っている1文**。`stage.speech.started` ごとに置き換える |
| 消える条件 | `stage.speech.ended`。**遮られたときも同じ経路**で消える（barge-in で文字だけ残らない） |
| 当たり判定 | **含めない。** 喋っている間ずっとデスクトップのクリックを奪うのは邪魔でしかない |
| タイムライン欠落 | **本文は出す。** 口が動かないことと、何を言ったか分からないことは別の話 |

**溜めて全文を出さない。** barge-in で途中で止まったとき、喋っていない文まで
吹き出しに残っていると「言っていないことを言ったことになる」。

#### ユーザーの発話も出す〔Phase 1〕

**Lumi の吹き出しとは逆の規則を持つ。**

| 規則 | Lumi（`stage.speech.started`） | ユーザー（`stage.user.said`） |
|---|---|---|
| 出す内容 | いま喋っている**1文** | 聞き取った**発話全体** |
| 消える条件 | `stage.speech.ended` | **Lumi が答え始めたとき**（`stage.speech.started`） |
| 位置 | 上・中央 | **下・右寄せ、明るい配色** |

**見た目を明確に分ける。** 誤認識を Lumi の発言と取り違えると、デバッグどころか
何が起きたのかの理解そのものを誤る。

消える条件をタイマーにしない。**Stage は判断しない**（`stage.*` で来た事実だけで決まる）。
ターンが成立しなかったとき吹き出しが残るのは意図的で、
**「聞こえていなかった」と「聞き違えた」を区別できる唯一の表示**である。

〔既知の粗さ〕下・右寄せなので、セットアップパネルが出ている間は重なりうる。
パネルは全部正常なら描画されないので、通常の会話中には起きない。

### 起動フェーズ — **キャラクターを出してよいかは Core が決める**〔Phase 0〕

> **これが「起動フェーズ」の唯一の定義場所**（DESIGN.md §12）。

Lumi は起動してすぐには喋れない。TTS エンジンの取得に数分、起動に十数秒かかる。
その間キャラクターを立たせておくと、**「立っているのに反応しない」= 壊れて見える。**

そこで **Core が起動フェーズを決めて `stage.setup.state` の `boot` として配る。**
Stage はそれを見て画面を切り替えるだけで、**自分では判断しない**（§2 の判定基準）。

| `boot` | 意味 | Stage が出すもの |
|---|---|---|
| `setup` | ユーザーの選択を待っている | 初回セットアップのパネル（取得するか尋ねる） |
| `installing` | エンジン / モデルを取得中 | 進捗つきのローディング |
| `starting` | TTS のプロセス起動中、または STT Provider のモデルロード中 | ローディング（初回は数分かかることを書く） |
| `blocked` | **セットアップが完了していない** | **不足項目と解決方法、そして [終了]** |
| `ready` | **キャラクターを出してよい** | キャラクター |

導出は決定論的な純粋関数（`lumi/setup/state.py` の `boot_phase()`）。
条件の全表と**評価の順序**は [setup.md](setup.md) §2b「起動フェーズへの反映」が唯一の定義場所。

**ローディングを出してよいのは、待ち終わったら `ready` に届くときだけ。**
不足が既に確定しているなら、待たせずに `blocked` を出す。

**出すのは常に1つだけ。** 上の表の右列は排他であり、ローディングとセットアップのパネルを
同時に出さない。両方出すと同じ状況が二重に書かれ、**どちらが本当か分からなくなる**
（実際に「エンジンを起動しています」が 2 枚並んだ）。

**ローディングの主語は Lumi。** 見出しは常に「Lumi を起動しています」で、
エンジンの取得・起動は**その内訳として下に添える**。エンジン名だけを大きく出すと、
Lumi ではなく外部エンジンを起動しているように見える。

**3つ（STT / LLM / TTS）が揃うまでキャラクターを出さない**〔[ADR-034](../decisions/ADR-034-gate-startup-on-complete-setup.md) で変更〕**。**
「取得しない」を選んだ場合も、取得に失敗した場合も `blocked` であり、`ready` にはならない。

> 〔取り消し〕rev.11 まではどちらも `ready` にし、「キャラクターを人質にしない」としていた。
> **同じ根拠が逆向きに効くことに気づいたため取り消した** — 「立っているのに反応しない = 壊れて見える」は、
> `starting` の十数秒より、**永久に返事が来ない状態にこそ強く当てはまる**。
> 取得の失敗が「起動できた」で上書きされることも防げていなかった（原則4 が表示に負けていた）。

`blocked` はローディングではない。**待っているのはユーザーの行動**なので、
スピナーを出さず、**不足している要素と、それぞれの解決方法**を並べる。
最後に [終了] を置き、「次回起動時にセットアップを再開できる」ことを伝える。

Ollama が `not_configured` のときは、その不足項目を専用の案内として先頭に出す。

- 見出し: **「Ollama が見つからない」**
- 本文: **「Ollama は、AI モデルを PC 上で動かすために使用します。」**と、
  Lumi が Ollama を通してローカル言語モデルを実行する説明
- 操作: 固定の公式ダウンロードページを開く [公式サイトを開く]。**[再チェック] は置かない**
- 背景動作: Coreへの再検出要求を1秒ごとに行う。実行ファイル初検出後は15秒まで
  **「Ollamaのセットアップを完了しています / Ollamaの起動を待っています」**として待つ。API 応答後は
  **「Ollama を検出しました」/「モデルを確認しています」**に切り替える

公式サイトの URL は Stage から渡さない。`shell_ollama_site_open` は Shell が保持する
固定 URL だけを既定ブラウザで開くため、任意 URL を開く能力を Stage に与えない。

モデルが無い場合は別の同意画面を出す。推奨モデル名・用途・概算サイズを表示し、
[Qwen 3.5 9B（約6.6 GB）をダウンロード] / [別のモデルを選ぶ] / [今は取得しない]
を並べる。「別のモデルを選ぶ」を押した画面では、Ollama の `/api/tags` から得た
既存ローカルモデルを先に表示する。既存モデルは [このモデルを使用] とし、
追加ダウンロードなしで `select` を返す。未取得の固定候補は名前・サイズを表示して
[ダウンロード] とする。この画面では見出しと説明文も「モデルを選択」に切り替える。
取得中はパーセントと `completed / total` を表示する。

ダウンロード確認（TTS / STT / LLM）またはセットアップ未完了（`blocked`）のカードが
表示されている間は、インスペクター・設定・終了・クレジットの操作バーをカードの上段に置く。
カードの下に操作を隠さず、セットアップ画面からも設定と診断へ到達できるようにする。

#### なぜ「喋れるようになってから」ではなく「エンジンが起動したら」出すのか

最初の合成には**さらに数秒**かかる（モデルのロード。実測 3.9 秒 →
[../measurements/phase0.md](../measurements/phase0.md)）。ここまで待つと無人の時間が延びる。

**キャラクターが先に出て、少し間があってから喋り出す方が自然**である。
逆にしてはいけないのは「喋り終わってから出る」で、これは音と姿が結びつかない。

**`ready` は音声入力開始のゲートでもある。** Core は `boot: ready` の配信が完了するまで
マイクと Reactive Loop を開始しない。ローディング画面の裏で発話を受理しない
（[ADR-033](../decisions/ADR-033-gate-voice-input-until-ready.md)）。
**`blocked` のあいだも開かない** — ゲートは `ready` のままなので、これは自動的に従う。

### キャラクターのモデルは Core が決め、Shell が配信する〔Phase 1・[ADR-029](../decisions/ADR-029-content-pack-asset-delivery.md)〕

| 誰が | 何を |
|---|---|
| **Core** | Content Pack を読み、**どのモデルか**を `stage.character.model` で配る（絶対パス） |
| **Shell** | そのファイルを WebView に読ませる（Tauri asset protocol + scope）。**Content Pack の外は拒否** |
| **Stage** | `PlatformShell.toAssetUrl` で受け取ったパスを URL 化して描く。**自分でパスを決めない** |

**Phase 0 の `DEFAULT_VRM_URL = "/character.vrm"` は消えた。** Stage が置き場所を知っているのは
「Stage が判断を持たない」に反していた（Phase 0 の暫定措置と明記されていたもの）。

**モデルが無いことも配る**（`path: null` + 理由）。「まだ来ていない」と「モデルを持たない
Content Pack」は別の状態で、後者だけがプレースホルダを**理由付きで**出す。

**宣言されたモデルファイルが欠けている、または読み込めない場合もプレースホルダへ落とすが、
音声入力と会話は継続する**（[ADR-044](../decisions/ADR-044-missing-character-model-keeps-conversation.md)）。
モデルは表現層のアセットであり、その失敗で有効な人格・音声設定と Reactive Loop を捨てない。

#### CSP の `connect-src` から `blob:` を外さない

GLTFLoader は **GLB に埋め込まれたテクスチャを `blob:` URL にしてから `ImageBitmapLoader`
で `fetch` する**。`connect-src` に `blob:` が無いと、この fetch だけが CSP に弾かれ、
**ジオメトリは出るがテクスチャが全部落ちる**（顔も服も無いのっぺりした灰色の人型になる）。
`img-src` の `blob:` では代替にならない（`<img>` 経路ではなく fetch 経路だから）。

`blob:` URL はそのページ自身しか作れないので、外部への送信経路にはならない。

**これは `pnpm build` でしか再現しない。** Tauri が CSP を注入するのは自分でフロントを
配信するときだけで、`tauri dev` は Vite dev server を直接読むため CSP が一切かからない
（`devCsp` も届かない）。**dev で動いたことは CSP が正しいことの証明にならない。**

### `credits` を Core に繋がない理由〔Phase 0〕

上と逆に、クレジットは**独立ウィンドウにする**（長い文書であり、キャラクターの
隣に出すものではない）。そのうえで **Core に接続しない**。理由は2つ。

1. **Core が落ちていてもクレジットは読めなければならない。** ライセンス文書の提示は
   Lumi の動作状態と無関係な義務であり、Core の生死に依存させると果たせない時がある
2. 接続すると Stage 側の WS が 2 本になり、`stage.*` に宛先の概念が要る（上と同じ理由）

**この決定は Phase 1 でも維持している。** Content Pack はモデルのクレジットを持つが、
クレジット画面はそれを読まない（読むと Core への接続が要り、上の理由が崩れる）。
**既定同梱モデルのクレジットは静的に載せてある** → [../licensing.md](../licensing.md) §4.5

代償として、Phase 0 のクレジットは「**使用中のもの**」に絞れず、
「Lumi が使いうるもの」を条件付きで全部載せることになる。
**足りないより多い方に倒す**（クレジットは欠けたときだけ違反になる）。
使用中のものに絞るのは Phase 1 の `Provider.attribution()` → [../licensing.md](../licensing.md) §6

### トレイメニュー〔Phase 0 / [ADR-047](../decisions/ADR-047-character-surface-interactions.md) で1項目追加〕

**「使いかた」「クレジット」「終了」の3つ。**

| 項目 | なぜ要るか |
|---|---|
| 使いかた | 操作パレットは**右クリックでしか開かない**。それを知らない人が持っている経路はトレイだけで、**そこに操作説明が無ければ何も見つからない** |
| クレジット | 「紹介画面など、**少し探せばわかる場所**」に置く義務がある（[../licensing.md](../licensing.md) §6） |
| 終了 | `stage` は枠なし・クリックスルー・タスクバー非表示のため、**トレイ以外に終了する手段が無い** |

**「使いかた」を先頭に置く。** 誤クリックで落ちるのが最悪なので **[終了] は最後**。
表示切替・設定・ホットキーは Phase 1。**トレイに AI の判断を出さない**（`shell.*` の規則）。

#### `blocked` の画面からも終了できる〔[ADR-034](../decisions/ADR-034-gate-startup-on-complete-setup.md)〕

セットアップ未完了で止まっているユーザーに、**「終了はトレイから」を要求しない。**
セットアップが終わっていない時点では、そもそもトレイに Lumi が居ることを知らない。

Stage から `shell.*` の `shell_app_quit` を呼ぶ（→ [../interfaces/shell.md](../interfaces/shell.md)）。
**判断は載らない**（押されたら終了する、以上）ので `shell.*` の規則に反しない。

### Tauri 2 固有の課題と対応

| 課題 | 対応 |
|---|---|
| **`setIgnoreCursorEvents` はあるが、Electron の `forward: true` 相当が無い** | **Rust 側で Win32 カーソル位置を監視し、キャラクター領域に入ったらクリックスルーを解除する**。Phase 0 スパイク（R2） |
| Windows での透過 + 常時最前面 | Phase 0 で検証。破綻したら `PlatformShell` 越しに Electron へ退避 |
| Python サイドカーの同梱 | Tauri の `externalBin`。**torch を避けてサイズを抑える**。Phase 0 で実測（R1） |

### ホバー検知の実装方針〔Provisional / Phase 0 で実装・実測済み〕

```
Stage が VRM の描画結果から当たり判定領域を算出 → shell.hit_region.set で Shell に渡す
  ↓
Rust 側で ~60Hz でカーソル位置をポーリング（GetCursorPos 相当）
  ↓  判定は Shell 側の純粋関数 decide_click_through / decide_hover_transition
領域内 → set_ignore_cursor_events(false) + shell.hover.state を Stage に通知
領域外 → set_ignore_cursor_events(true)
```

**判定を Shell 側で行う**（当初案は「Stage が比較する」だった）。理由は2つ。

1. 60Hz のカーソル位置を毎周期 Stage に送って往復させると、`shell.*` の
   「1ms 以下であるべきもの」という規則を守れない
2. Stage が固まっている間もクリックスルーの切り替えは正しく動く必要がある

Stage が渡すのは**領域だけ**で、判断は渡さない。この経路に AI の判断は乗らない。
領域が未設定のときは**クリックスルーを維持する**（Stage が壊れたときにデスクトップが
操作不能になる方が危険なため。ここだけは fail-closed に倒さない）。

座標は **Stage ウィンドウのクライアント領域を原点とする物理ピクセル**。
CSS ピクセルからの変換は Stage 側の責務（混在 DPI → 未確定事項 #15）。

実測値（ポーリングの CPU コスト）→ [../measurements/phase0.md](../measurements/phase0.md)

### AIRI から借りる運用知見

Electron 版の知見だが、Tauri でも同じ問題に当たる。

| 知見 | 内容 |
|---|---|
| 常時最前面 | 他アプリがフルスクリーンでも前面を維持する必要がある |
| 非フォーカス表示 | 表示時にフォーカスを奪わない |
| バックグラウンド抑制の無効化 | 非アクティブ時にレンダリングが止まるとアイドルモーションが固まる |
| コンテンツ保護 | 画面共有時に映らないようにする選択肢 |
| **自分自身を deny リストに入れる** | 自己操作の防止（Invariant 8 と同じ発想） |
| **ウィンドウ設定を純粋関数に切り出す** | ユニットテスト可能にする（AIRI の `window-contract.ts` の作法） |

最後の1つは特に採用する。ウィンドウ設定を純粋関数にすることで、Tauri に依存せずテストできる。

### `PlatformShell` 抽象

Electron への退避路を確保する。→ [../interfaces/shell.md](../interfaces/shell.md)

**Phase 0 で interface を定義し、Tauri 実装を作る。** Stage 側は `PlatformShell` 越しにのみ Shell と話すので、実装を差し替えても Stage は変わらない。

---

## 2. Stage（React + TypeScript + Zustand）

**表現のみ。ビジネスロジックを持たない。**

| 責務 | 内容 |
|---|---|
| 描画 | VRM（three + `@pixiv/three-vrm`）、表情、モーション、リップシンク |
| 吹き出し | テキスト表示 |
| Widget | sandboxed iframe のホスト + **Widget Broker**（B6 の境界） |
| 設定 UI | 表示と変更（**保存は Core**） |
| 権限プロンプト | 独立ウィンドウ |
| Inspector | 開発時のみ |

### モジュール構成

```
stage/src/
├── main.tsx        `stage` ウィンドウの entry。**ここだけが stage.css を読む**
├── mount.tsx       `#root` を見つけて render するだけ。**Core も locale も知らない**
├── App.tsx         キャラクター窓の組み立て（当たり判定の合成・パレットの開閉）
├── styles/
│   ├── tokens.css  デザイントークン。**各 entry が最初に読む。共有する意味色・反復する値を置く**
│   ├── stage.css   **キャラクター窓専用。** 透過ウィンドウの reset を含む
│   └── document.css `credits` と `help` が共有する文書レイアウト
├── mount.test.ts   **Core に繋がない窓が Core に到達しない**ことの静的検査
├── core/           Core との WS・protocol・payload・ストア（`stage.*` / `panel.*`）
├── platform/       `PlatformShell` と Tauri 実装（`shell.*`）
├── character/      VRM の読み込みと描画、表情・リップシンク・アイドル
├── speech/ audio/ actions/ setup/ settings/ memory/ inspector/  各画面
├── panel/          3つのパネル窓の共通枠と entry（ADR-042）
├── credits/ help/  **Core に繋がない2枚**（§1）
└── i18n/           翻訳カタログとロケール解決
```

**どこに置いてよいかの条件**（`shell/` 側で「条件を書かなかったために `tray.rs` が
その他コマンド置き場になった」のと同じことを繰り返さないために書いておく）。

| 置き場所 | 置いてよいもの | 置いてはいけないもの |
|---|---|---|
| `mount.tsx` | **すべての entry が共通で行うこと**だけ | **`core/*` と `i18n/provider` への import。** ここに1行足すと `credits` と `help` が Core 接続コードを抱き込む |
| `styles/tokens.css` | 2箇所以上で使う値 | ルール（セレクタ）。ここは値だけ |
| `styles/stage.css` | キャラクター窓だけが使うもの | パネル窓が使うクラス |
| `panel/panel.css` | パネル窓が使うもの | キャラクター窓だけのもの |
| `credits/` `help/` | その画面固有のもの | `core/` `platform/` `@tauri-apps` への import |

**CSS は entry が読む順序がカスケード順そのものなので、`@import` で引き込まない。**

```text
main.tsx         → tokens.css, stage.css
panel/main.tsx   → tokens.css, panel.css
credits/main.tsx → tokens.css, document.css, credits.css
help/main.tsx    → tokens.css, document.css, help.css
```

**`stage.css` をパネル窓が読んではいけない。** `stage.css` の reset は
`html, body, #root` に「ビューポート全体・overflow 隠し・テキスト選択不可」を与える。
デスクトップに浮く窓には正しく、文書型の窓には誤りである。
実際、パネル窓がこれを読んでいたために **記憶窓の本文が選択・コピーできず、
設定窓とインスペクター窓は内容が切れていた**（スクロールの復帰が
`#root[data-panel="memory"]` にだけ書かれていた）。
分離によって両方とも解消し、その場しのぎの上書きも不要になった。

**`.settings__src` は `styles.css` と `panel.css` の両方に別の値で定義されていた。**
パネル窓は両方を読むので、どちらが勝つかは `main.tsx` の import 順だけで決まっていた。
分離のとき `panel.css` の1箇所に統合した。

### AIRI から借りないこと

AIRI は Pinia ストアにビジネスロジックを置いている（`stage-ui/src/stores/` に Agent オーケストレーション、記憶、自律スケジューラが同居）。

**Lumi ではロジックは Core にのみ存在する。** Zustand ストアは「今何を描画すべきか」だけを持つ。

判定基準: **Stage のストアから読める値は、すべて Core が `stage.*` で配信したものであるべき。** Stage が自分で計算して状態を作っていたら、それはロジックが漏れている。

### 2つの経路

| namespace | 経路 | 内容 |
|---|---|---|
| `shell.*` | Tauri IPC | ウィンドウのドラッグ、クリックスルー切替、ホバー状態。**1ms 以下であるべきもの** |
| `stage.*` | WS (Core) | キャラクターの発話・表情・Widget・設定 |

> **`shell.*` は絶対に AI の判断を運ばない。`stage.*` は絶対に OS 特権を要求しない。**

### ウィンドウの移動と大きさ〔Phase 0。簡易版〕

| 操作 | 経路 | 実装 |
|---|---|---|
| **移動** | `shell.*` | `alt` + 左ドラッグ、または中ドラッグ → OS のウィンドウ移動に委ねる |
| **大きさ** | `shell.*` | ホイール → 現在の大きさに倍率をかける |
| **左クリック / 左ドラッグ** | — | **キャラクターとのインタラクション用に予約する**〔[ADR-047](../decisions/ADR-047-character-surface-interactions.md)〕。中身は未定 |
| **右クリック** | — | アプリ操作パレット（§1） |

**どれも、その時点で画面に出ている操作面の上でだけ効く。** キャラクターをまだ出せない
`setup` / `installing` / `starting` / `blocked` では、パネルが一時的な操作面になる。

**この表は `stage` ウィンドウが出しているものすべてに同じように効く**
〔[ADR-047](../decisions/ADR-047-character-surface-interactions.md)〕。ローディング・
セットアップのパネルも例外ではない。**あの画面はキャラクターの代役であって、ダイアログではない。**
ホバーで何かが出るか、素の左ドラッグで動くかが**Lumi がその時何をしているかで変わる**なら、
誰にも覚えられない。セットアップ中に素の左ドラッグで動かせなくなるのは実際の劣化だが、
[終了] も [Ollama を開く] も**カード自身が持っている**（[ADR-034](../decisions/ADR-034-gate-startup-on-complete-setup.md)）ので行き止まりにはならない。
ただしボタンなどの操作要素と、コピー可能なコマンド欄では移動・拡大縮小を行わない。
Stage でこの除外を明示する要素には `data-window-gesture="exclude"` を付ける。
`WINDOW_GESTURE_EXCLUSION` は `closest()` で判定するため、属性を付けた要素とその子孫の
どちらからも、ネイティブのウィンドウドラッグ開始や倍率変更を要求しない。
当たり判定の外はクリックスルーなので、背後のウィンドウの操作を邪魔しない。

#### 既定の位置と大きさ — **画面から計算する**

| | 決め方 |
|---|---|
| 大きさ | **作業領域の高さの 50%**（縦横比 2:3）。1440p で 464×696、1080p で 348×522 |
| 位置 | **作業領域の右下。右に 24px の余白、下は 0**（タスクバーに接地させる） |

作業領域 = タスクバーなどを除いた範囲。ここに収めれば下端が隠れない。
固定の px 値にしないのは、**画面が変われば適切な大きさも変わる**ため。

**下の余白を 0 にするのは、空けるとキャラクターが宙に浮いて見えるため。**
デスクトップに住んでいるものは、床の上に立っている方が自然である。

**拡大縮小は右下の角を固定する。** 左上を固定すると、拡大のたびにキャラクターが
画面の外へはみ出していく。立っているものは足元が動かない方が自然でもある。

> **★ Tauri のビルダーの `position` だけでは効かない**〔2026-08-15 実測〕。
> Windows で数十 px ずれた場所に出る。**生成後にもう一度 `set_position` する。**

**クランプは Shell 側の純粋関数**（`compute_scaled_size`）。倍率を Stage が計算して
「この大きさにしろ」と渡す形にはしない。**Stage が画面の外に出せる大きさを要求できてはいけない。**

#### 保存しない

**位置も大きさも保存しない**（Phase 0）。設定の保存形式は未確定（roadmap 未確定事項 #9 / Phase 1）であり、
**保存先を今決めると Phase 1 で移行が要る**。次の起動では既定の位置・大きさに戻る。

---

## 3. Character API

```python
character.speak(text, emotion=None, priority=Priority.NORMAL,
                behavior=Behavior.QUEUE)     # queue | interrupt | replace
character.set_expression(intent, intensity, duration=None)
character.play_motion(name, loop=False, blend_ms=300)
character.look_at(target)
```

### `set_expression` は「意図」を受け取る

**パラメータではない。**

VRM は名前付きブレンドシェイプの合成、Live2D は生パラメータの直接操作と、表情モデルが**根本的に異なる**。`CharacterRenderer` インターフェースがパラメータを露出すると、Live2D 追加時に必ず破綻する。

```python
@dataclass(frozen=True)
class ExpressionIntent:
    emotion: Emotion         # happy | sad | angry | surprised | think | curious | neutral | ...
    intensity: float         # 0.0-1.0
    blend_ms: int
```

Renderer が表現できない意図は、**Renderer 側で**最も近い意図にフォールバックする。Core は知らない。

詳細 → [../interfaces/renderer.md](../interfaces/renderer.md), [ADR-009](../decisions/ADR-009-renderer-intent-based.md)

### 表情の決まり方

```
Mood（Internal State。持続。慣性と減衰）    ← ベースライン
  +
<|ACT|> マーカー（瞬間値）                  ← この発話だけ
  =
最終的な ExpressionIntent
```

同じ「驚く」でも機嫌のいいときと悪いときで違って見える。→ [world-state.md](world-state.md)

### インラインマーカー

LLM ストリーム内の `<|ACT {"emotion":"happy","intensity":0.7}|>` を使う（AIRI のアプローチを借用）。

- ストリーミング中にパースし、マーカーは**音声化前に除去**する
- パース失敗時はマーカーごと落とす（読み上げない）
- エスケープ: `<{'|'}` / `{'|'}>`

### リップシンク

**口の形は TTS の音素列から、時間は音声の長さから**〔Confirmed。2026-08-15〕。
エンジンが音素長を返すとは限らない（AivisSpeech は返さない）ことが実測で分かっている。

**型（`VisemeSpan` / `VisemeTimeline`）と `stage.speech.*` の契約の唯一の定義場所は
[../interfaces/renderer.md](../interfaces/renderer.md)。** ここには置かない。

要点だけ: **Core は時間軸つきの意図を1回送り、時刻は Stage の時計で進める。**
60Hz で送ると Stage が詰まったときに口が固まる（ホバー検知と同じ理由）。

---

## 4. Widget / Gamelet

〔原則のみ Confirmed。API 詳細は Phase 7〕

> **Widget iframe を信用してはならない。真のセキュリティ境界は Widget Broker である。**

```
Widget iframe → postMessage → Widget Broker (Stage内) → Core (Permission Kernel)
                              ↑ ここが Security Boundary (B6)
```

**iframe sandbox の位置づけ・Broker の責務・生成ゲームの追加制約は
[../contracts/security-boundaries.md](../contracts/security-boundaries.md) の B6 が唯一の定義場所。**

UI 側の実装要点だけ:

- Broker は Stage 内に置くが、**Stage の他のコードから直接呼べない**（メッセージ経路を1本に絞る）
- Widget は `widget` lane の Class B Tool として Core に到達する（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)）

---

## 5. Inspector（開発時のみ）

**「なぜ今それを言ったのか」を後から追跡できることは設計要件である。** これが無いと Phase 6 でチューニング不能になる。

| 表示項目 | 内容 |
|---|---|
| Activity ツリー | 現在の Activity と状態、子 Tool の状態（**乖離が見える**） |
| Drive | 各 Drive の値と `effective_drive` の内訳（**なぜ発火した/しなかったか**） |
| World / Internal State | facet 一覧（期限切れは灰色）、mood / fatigue |
| 記憶検索 | 直近の検索結果と**採用理由**（スコアの内訳）、落とされたもの |
| 権限 | 判断履歴、Provenance の伝播経路 |
| レイテンシ | 区間別の p50/p95/p99 |
| リソース | VRAM / RAM 占有 |

Phase 1 から最小版（Activity ツリー + レイテンシ）を作る。

### 独立ウィンドウにする〔2g。Phase 1 の判断を [ADR-042](../decisions/ADR-042-panel-windows-and-panel-role.md) が置き換えた〕

**Phase 1 では `stage` ウィンドウの中に描いていた。** 理由は
「別ウィンドウが `stage` role で繋ぐと**キャラクター側の接続を奪う**」であり、
**その理由はいまも正しい。** 置き換わったのは結論ではなく前提のほうで、
Inspector は `stage` ではなく **`panel` role** で繋ぐ。キャラクターの接続は誰も奪わない。

| 規則 | 内容 |
|---|---|
| 置き場所 | 独立ウィンドウ（`inspector`）。`stage` の操作列の ◎ から開く |
| 接続 | **`panel` role。** `panel.inspector.state` を受け取るだけ |
| 送信先 | **開いている panel だけ。** 閉じていれば送らない——`stage` の接続には**もう流れない** |
| 送信契機 | Activity 遷移（`DomainEvent` の購読）と、ターンのレイテンシ確定 |
| **送信経路** | **購読ハンドラの中で送らない**（下記） |

#### ★ Inspector の描画を barge-in の経路に載せない

`EventBus._dispatch` は購読者を **`await` する**。Activity の遷移は preempt 中に起きるので、
ハンドラの中で WS 送信すると、**ユーザーが喋ってから Lumi が黙るまでの間に Inspector の描画が挟まる。**

そこで購読ハンドラは**フラグを立てるだけ**にし、送信は別タスクが行う。
副次的に、1回の preempt が生む4つの遷移（旧→`interrupt_requested` / 新→`accepted` /
新→`running` / 旧→`cancelling`）が**1回のスナップショットにまとまる。**

**常にスナップショットを作る**（見ていなくても）。フラグで止めると、**見たくなったときに
Core の再起動が要る。** ペイロードは小さな dict が1ターンに数回であり、止める理由がない。

〔2g〕**送信自体は inspector ウィンドウが開いているときだけ起きる。** これは
「作るのを止める」のとは別で、`notify(Role.PANEL, ...)` の宛先が0本なら黙って落ちるだけである。

---

## 5b. 記憶ウィンドウ〔2g〕

**ユーザーが自分の記憶を見て、直して、消せること。** これが無いと、間違った記憶が永久に残る。
→ [memory.md](memory.md) / [../contracts/privacy.md](../contracts/privacy.md)

| 操作 | 送るもの | 規則 |
|---|---|---|
| 検索・一覧 | `panel.memory.search` | 既定は**新しい順**。検索語があれば 2e の検索を通す |
| 内容を直す | `panel.memory.edit` | **上書きではなく supersede。** 直した記録が残る |
| 忘れさせる | `panel.memory.forget` | **archive ではなく削除**（privacy.md §2 の「ユーザーが消したから」） |
| 「これで合ってる」 | `panel.memory.confirm` | **`user_confirmed` への昇格**。→ 下記 |
| エクスポート | `panel.memory.export` | **出力は平文。書き出す前にそう言う** |
| 全部消す | `panel.memory.erase_preview` → `panel.memory.erase` | **何が何件消えるかを見せてから消す** |

### ★ 記憶ウィンドウは Invariant 7 の昇格経路である

`tainted → trusted` の昇格は **`MemoryStore.confirm()` の1箇所だけ**であり、
そこを呼べるのは**このウィンドウのボタンだけ**である。

したがって **Lumi 自身がこのボタンを押せてはならない。**
`WindowKind::is_protected()` は Lumi の全ウィンドウに対して true を返すので、
`os.input.*` の対象にできない（Invariant 8 / B3）。**この性質に依存していることを、
ここに書いておく**——「全部 protected」を将来ゆるめるときに、これが壊れる。

### 消す前に何が消えるかを見せる

`erase_preview` は [../contracts/privacy.md](../contracts/privacy.md) §2 の表の**行ごとに件数**を返す。
**0件の行も返す。** 「その種類のデータは無い」と「その種類を消し忘れている」は別のことであり、
表から行が消えると後者が見えなくなる。

### マイクが開いていることは `stage` に出す〔2g〕

**記憶ウィンドウではなくキャラクターの隣。** マイクが開いているかどうかは
**閉じられるウィンドウの中にあってはいけない**——見えていないことが既定になる。

| 規則 | 内容 |
|---|---|
| 表示 | `stage.audio.mic` が `open` の間、キャラクターの近くに常時表示 |
| ミュート | 同じ場所を押すと `stage.audio.mute`。**Core が実際に止めてから表示が変わる** |
| 当たり判定 | **常に含める。** ホバーで消える操作にしない（消えている間に喋ってしまう） |

---

## 6. Phase ごとの実装範囲

| Phase | 内容 |
|---|---|
| **0** | 透過 / 最前面 / クリックスルー / ホバー検知 / VRM 表示 / アイドルモーション / リップシンク / `PlatformShell` 定義 / `os.*` の検証層 / **初回セットアップ UI** / **クレジット画面** |
| **1** | 吹き出し / Inspector 最小版 / 設定 UI 骨格 / **表情は Stretch** |
| **2** | 記憶 UI（閲覧・編集・削除・確認） |
| **3** | Inspector に Drive / World / Internal を追加 |
| **4a** | **権限プロンプト UI** / 監査ログ閲覧 |
| **4c** | 保護対象ウィンドウのキャプチャ除外（`WDA_EXCLUDEFROMCAPTURE`）→ [../contracts/invariants.md](../contracts/invariants.md) の Invariant 8 |
| **7** | Widget Broker / sandboxed iframe / Widget API |
| **9** | Live2D Renderer |

---

## 6b. 表示言語（i18n）〔Phase 1〕

Stage と Shell が生成するユーザー向け文言は **日本語 (`ja`) と英語 (`en`)** を持つ。
設定の `locale` は `auto` / `ja` / `en`。`auto` では OS / WebView の優先言語から解決し、
`ja` で始まる言語タグだけを日本語、それ以外を英語にする。言語タグが取得できない場合も
英語へフォールバックする。設定画面で変更すると保存成功後に即時反映する。

これは判断や製品状態ではなく**表示だけの関心事**である。Core はロケールを持たず、
Stage は Core が送った識別子・数値・エラー理由を、選んだロケールの文言へ写像する。
未知のエラー理由は翻訳の有無にかかわらず原文の識別子を表示し、黙って捨てない。

| 領域 | ロケールの取得元 | 実装上の規則 |
|---|---|---|
| Stage / クレジット | Core の `locale`。`auto` は `navigator.languages` → `navigator.language` | 翻訳カタログを単一定義にし、`document.documentElement.lang` も同じ値にする。独立したクレジット画面の起動前表示だけは、同一 WebView origin の前回値をキャッシュとして使い、Core 接続後の値を正とする |
| Shell のトレイ / ネイティブウィンドウ | Stage が解決済みの `ja` / `en` を `shell.locale.set` で通知。通知前は OS のユーザー既定ロケール | トレイと開いているクレジットウィンドウのタイトルを即時更新する。これは表示だけであり AI 判断を運ばない |
| Core 由来の会話本文・モデル名・コマンド | 翻訳しない | ユーザーの入力や Core の生成物を UI 文言として扱わない |
| ライセンス全文 | 翻訳しない | 同梱した原文をそのまま提示し、周囲の案内文だけを翻訳する |

初回セットアップの同意文は、どちらの言語でも取得主体・通信先・サイズ・取得しない場合の
挙動を省略しない。翻訳によって consent の情報量を変えない。

---

## 7. テスト

| # | テスト |
|---|---|
| 1 | **ウィンドウ設定の純粋関数のユニットテスト**（透過 / 最前面 / クリックスルーの組み合わせ） |
| 2 | ホバー判定の純粋関数のユニットテスト |
| 3 | `shell.*` に AI 判断の型が含まれない（静的検査） |
| 4 | `stage/` から `os.*` を参照していない（静的検査） |
| 5 | Stage のストアが Core 配信以外の値を持たない |
| 6 | `<|ACT|>` マーカーが音声化テキストから除去される |
| 7 | パース失敗したマーカーが読み上げられない |
| 8 | Renderer が未知の emotion をフォールバックする |
| 9 | 表情が Mood + ACT の合成になる |
| 10 | 権限プロンプトウィンドウが `os.input.*` の対象にならない（Shell / Core の二重確認） |
| 11 | Widget Broker が宣言外の capability を拒否する（Phase 7） |
| 12 | `ja-JP` は日本語、未知・空の言語タグは英語へ解決する |
| 13 | セットアップの状態・失敗理由が日本語と英語の双方で区別される |
| 14 | `locale` は `auto` / `ja` / `en` だけを保存でき、変更通知で Stage と Shell が即時更新される |
| 15 | **`boot` が `blocked` のときキャラクターも吹き出しも描かれない**〔ADR-034〕 |
| 16 | **`blocked` のとき、不足している要素すべてが解決方法つきで並ぶ**（1つだけ出して終わらない）〔ADR-034〕 |
| 17 | **未知の `boot` 値を `ready` に丸めない**（キャラクターを出す側に倒さない） |
| 18 | `stage` の操作メニューからクレジット画面を開け、Lumi を終了できる |
