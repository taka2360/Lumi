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

### ウィンドウ一覧

| ウィンドウ | 用途 | 特性 |
|---|---|---|
| `stage` | キャラクター本体 | 透過 / 最前面 / クリックスルー / 非フォーカス |
| ~~`bubble`~~ | 吹き出し | **Phase 1 では作らない。`stage` の中に描く**（下記） |
| `permission` | **権限プロンプト** | **フォーカス必須 / Invariant 8 の保護対象** |
| `credits` | クレジットとライセンス（トレイ / `stage` の操作メニュー → クレジット） | 通常ウィンドウ。**Core に接続しない**（内容は静的） → [../licensing.md](../licensing.md) §6 |
| `settings` | 設定 | 通常ウィンドウ |
| `widget` | Widget / Gamelet | 通常ウィンドウ（Phase 7） |
| ~~`inspector`~~ | 開発用 | **`stage` の中に描く**（§5） |

### 初回セットアップ UI は `stage` ウィンドウの中に置く〔Phase 0〕

独立ウィンドウにしない。理由は **Core ↔ Stage の WS 接続を 1 本に保つため**。
ウィンドウを増やすと接続が増え、「どの Stage に送るか」という宛先の概念が
`stage.*` に必要になる（B2 の検証対象が増える）。Phase 0 で払う価値がない。

- キャラクターの隣にパネルとして出す。当たり判定領域はキャラクターとパネルの**和**を渡す
- クリックスルーはパネル上でも解除される（領域に含まれるため）
- 状態と進捗は Core が `stage.setup.state` で配信する。**Stage は表示するだけ**

→ [setup.md](setup.md)

### アプリ操作は `stage` ウィンドウからも到達できる〔Phase 1〕

トレイを知らないユーザーにも基本操作が見つかるよう、`stage` ウィンドウのホバー操作領域に
**[クレジットとライセンス] と [終了]** を置く。トレイの項目は代替経路として維持する。

- 操作領域は Inspector / 設定と同様、ホバー中だけ表示し、表示中だけヒット領域へ含める
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

### トレイメニュー〔Phase 0〕

Phase 0 では **「クレジット」と「終了」の2つだけ**置く。

| 項目 | なぜ Phase 0 で要るか |
|---|---|
| クレジット | 「紹介画面など、**少し探せばわかる場所**」に置く義務がある（[../licensing.md](../licensing.md) §6） |
| 終了 | `stage` は枠なし・クリックスルー・タスクバー非表示のため、**トレイ以外に終了する手段が無い** |

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
| **移動** | `shell.*` | キャラクター、または起動・セットアップパネルの上で左ドラッグ → OS のウィンドウ移動に委ねる |
| **大きさ** | `shell.*` | キャラクター、または起動・セットアップパネルの上でホイール → 現在の大きさに倍率をかける |

**どちらも、その時点で画面に出ている操作面の上でだけ効く。** キャラクターをまだ出せない
`setup` / `installing` / `starting` / `blocked` では、パネルが一時的な操作面になる。
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

### 独立ウィンドウにしない〔Phase 1 Step G〕

**`WsServer` は role ごとに接続を1本しか持たない**（`_connections: dict[Role, Connection]`）。
Inspector が別ウィンドウとして `stage` role で繋ぐと、**キャラクター側の接続を奪う。**
吹き出しと同じ結論だが、理由はより強い — こちらは「望ましくない」ではなく**動かない**。

| 規則 | 内容 |
|---|---|
| 置き場所 | `stage` ウィンドウの左下。既定は畳んだ状態 |
| 表示契機 | **ホバー時のみ表示**（Lumi上にカーソルがあるとき、またはパネル展開中）。常時表示しない |
| 当たり判定 | **表示中のみ含める。** 非表示時はクリックスルーを阻害しないよう除外する |
| 送信契機 | Activity 遷移（`DomainEvent` の購読）と、ターンのレイテンシ確定 |
| **送信経路** | **購読ハンドラの中で送らない**（下記） |

#### ★ Inspector の描画を barge-in の経路に載せない

`EventBus._dispatch` は購読者を **`await` する**。Activity の遷移は preempt 中に起きるので、
ハンドラの中で WS 送信すると、**ユーザーが喋ってから Lumi が黙るまでの間に Inspector の描画が挟まる。**

そこで購読ハンドラは**フラグを立てるだけ**にし、送信は別タスクが行う。
副次的に、1回の preempt が生む4つの遷移（旧→`interrupt_requested` / 新→`accepted` /
新→`running` / 旧→`cancelling`）が**1回のスナップショットにまとまる。**

**常に配信する**（見ていなくても）。フラグで止めると、**見たくなったときに Core の再起動が要る。**
ペイロードは小さな dict が1ターンに数回であり、止める理由がない。

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
