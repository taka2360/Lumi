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
| `credits` | クレジットとライセンス（トレイ → クレジット） | 通常ウィンドウ。**Core に接続しない**（内容は静的） → [../licensing.md](../licensing.md) §6 |
| `settings` | 設定 | 通常ウィンドウ |
| `widget` | Widget / Gamelet | 通常ウィンドウ（Phase 7） |
| `inspector` | 開発用 | 通常ウィンドウ（dev のみ） |

### 初回セットアップ UI は `stage` ウィンドウの中に置く〔Phase 0〕

独立ウィンドウにしない。理由は **Core ↔ Stage の WS 接続を 1 本に保つため**。
ウィンドウを増やすと接続が増え、「どの Stage に送るか」という宛先の概念が
`stage.*` に必要になる（B2 の検証対象が増える）。Phase 0 で払う価値がない。

- キャラクターの隣にパネルとして出す。当たり判定領域はキャラクターとパネルの**和**を渡す
- クリックスルーはパネル上でも解除される（領域に含まれるため）
- 状態と進捗は Core が `stage.setup.state` で配信する。**Stage は表示するだけ**

→ [setup.md](setup.md)

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

### 起動フェーズ — **キャラクターを出してよいかは Core が決める**〔Phase 0〕

> **これが「起動フェーズ」の唯一の定義場所**（DESIGN.md §12）。

Lumi は起動してすぐには喋れない。TTS エンジンの取得に数分、起動に十数秒かかる。
その間キャラクターを立たせておくと、**「立っているのに反応しない」= 壊れて見える。**

そこで **Core が起動フェーズを決めて `stage.setup.state` の `boot` として配る。**
Stage はそれを見て画面を切り替えるだけで、**自分では判断しない**（§2 の判定基準）。

| `boot` | 意味 | Stage が出すもの |
|---|---|---|
| `setup` | ユーザーの選択を待っている | 初回セットアップのパネル |
| `installing` | エンジンを取得中 | 進捗つきのローディング |
| `starting` | エンジンのプロセスを起動中 | ローディング（初回は数分かかることを書く） |
| `ready` | **キャラクターを出してよい** | キャラクター |

導出は決定論的な純粋関数（`lumi/setup/state.py` の `boot_phase()`）。

**出すのは常に1つだけ。** 上の表の右列は排他であり、ローディングとセットアップのパネルを
同時に出さない。両方出すと同じ状況が二重に書かれ、**どちらが本当か分からなくなる**
（実際に「エンジンを起動しています」が 2 枚並んだ）。

**ローディングの主語は Lumi。** 見出しは常に「Lumi を起動しています」で、
エンジンの取得・起動は**その内訳として下に添える**。エンジン名だけを大きく出すと、
Lumi ではなく外部エンジンを起動しているように見える。

**「取得しない」を選んだ場合も `ready` になる。** 喋らないが Lumi は起動している
（[ADR-019](../decisions/ADR-019-tts-engine-distribution.md) 原則3）。**取得に失敗した場合も `ready`**。
壊れていることは SetupPanel が言う。**キャラクターを人質にしない。**

#### なぜ「喋れるようになってから」ではなく「エンジンが起動したら」出すのか

最初の合成には**さらに数秒**かかる（モデルのロード。実測 3.9 秒 →
[../measurements/phase0.md](../measurements/phase0.md)）。ここまで待つと無人の時間が延びる。

**キャラクターが先に出て、少し間があってから喋り出す方が自然**である。
逆にしてはいけないのは「喋り終わってから出る」で、これは音と姿が結びつかない。

### `credits` を Core に繋がない理由〔Phase 0〕

上と逆に、クレジットは**独立ウィンドウにする**（長い文書であり、キャラクターの
隣に出すものではない）。そのうえで **Core に接続しない**。理由は2つ。

1. **Core が落ちていてもクレジットは読めなければならない。** ライセンス文書の提示は
   Lumi の動作状態と無関係な義務であり、Core の生死に依存させると果たせない時がある
2. 接続すると Stage 側の WS が 2 本になり、`stage.*` に宛先の概念が要る（上と同じ理由）

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
| **移動** | `shell.*` | キャラクターの上で左ドラッグ → OS のウィンドウ移動に委ねる |
| **大きさ** | `shell.*` | キャラクターの上でホイール → 現在の大きさに倍率をかける |

**どちらもキャラクターの上でだけ効く。** 当たり判定の外はクリックスルーなので、
背後のウィンドウの操作を邪魔しない。

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
