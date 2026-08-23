# ADR-042: 設定・インスペクタ・記憶を独立ウィンドウにし、`panel` role を新設する

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-08-23 |
| **関連** | [ADR-022](ADR-022-wire-contract.md), [ADR-028](ADR-028-stage-initiated-request.md), [architecture/ui.md](../architecture/ui.md), [contracts/security-boundaries.md](../contracts/security-boundaries.md), [contracts/privacy.md](../contracts/privacy.md), [interfaces/shell.md](../interfaces/shell.md) |

## Decision

**設定・インスペクタ・記憶を `stage` ウィンドウの中から出し、それぞれ独立ウィンドウにする。**
`credits` と同じ扱いになり、`stage` ウィンドウに残るのは**キャラクター・吹き出し・
セットアップ・マイク表示・アイコンだけの操作列**である。

そのために **role `panel`（namespace `panel.`）を新設する。**

| 規則 | 内容 |
|---|---|
| 接続数 | **`panel` だけ複数接続を許す。** `shell` / `stage` は従来どおり各1本 |
| Core → panel | **`notify` のファンアウトのみ。** 開いている panel 全部に送る。0個なら黙って落とす |
| **`invoke`** | **panel に対して禁止**（`ValueError`）。答えを待つコマンドは宛先が一意でなければ成立しない |
| panel → Core | 従来の inbound と同じ。`panel.*` だけが届き、登録が allowlist（ADR-028） |
| token | **3本目を作る。** Shell は**要求元ウィンドウのラベル**で渡すトークンを決め、`stage` トークンは `stage` ウィンドウにしか渡さない |

ウィンドウは `settings` / `inspector` / `memory` の3つ。`stage` の操作列は
**文字ではなくアイコン（シグニファイア）の横並び**にする。

## Reason

**記憶 UI がホバーパネルに入らない。** 2g が要求するのは一覧・編集・削除・エクスポート・
全消去であり、そのうち削除と全消去は**確認を伴う**。ホバー中だけ表示される領域に置くと、
**確認ダイアログを読もうとカーソルを動かした瞬間に消える。** 消す操作の確認が、
カーソルの位置という無関係な理由で消えるのは、UI の事故ではなく安全性の欠陥である。

**元の理由はもう成立しない。** ui.md は「接続を1本に保つ」ためだと書いていたが、
その具体的な理由は「**どの Stage に送るか**という宛先の概念が `stage.*` に必要になる」だった。
宛先が必要なのは**答えを待つ `invoke` だけ**である（`stage.setup.prompt` がこれに当たる）。
**panel に `invoke` を禁止すれば、宛先の概念は最初から要らない。**

インスペクタについて ui.md が挙げた理由（別ウィンドウが `stage` role で繋ぐと
**キャラクターの接続を奪う**）は正しく、いまも正しい。だから panel は
**`stage` とは別の role** であり、キャラクターの接続は誰にも奪われない。

副次的に、**毎ターンの inspector ペイロードがキャラクターの接続から消える。**
inspector の送信は barge-in の経路に載せない設計（ui.md §5）だが、そもそも
**同じ接続を通らなくなる**ほうが強い。

## Alternatives

**`stage` role を複数接続にする。**
利点: role が増えない。採らない理由: `invoke(Role.STAGE, "stage.setup.prompt")` の
宛先が壊れる。セットアップの問いが**どのウィンドウに出たのか分からなくなり**、
答えが二重に返る可能性がある。

**ウィンドウごとに role を作る**（`settings` / `inspector` / `memory`）。
利点: 宛先が常に一意で、`invoke` も使える。採らない理由: **token と B2 の検証面が
ウィンドウの数だけ増える。** 3つとも「Lumi 自身のローカル WebView で、`os.*` に触れない」
という同じ信頼境界にあり、**境界が同じものを別の role にすると境界の意味が薄まる。**

**汎用パネルウィンドウ1枚 + タブ。**
利点: 接続が1本で済む。採らない理由: 「別画面」にならない。設定を見ながら記憶を直す、
という当たり前の使い方ができない。

**panel を Core に繋がず、Shell 経由で stage ウィンドウに中継させる。**
採らない理由: **Shell が Core の状態を運ぶことになる。** Shell は OS 特権プリミティブのみ
という責務（Invariant 1 / 6）を壊す。

## Trade-offs

**受け入れるコスト**
- 接続数が増える。panel が3つ開いていれば WS 接続は5本
- token が3本になる。Shell の受け渡しに**ラベルによる分岐**が入る
- `notify(Role.PANEL, ...)` は宛先0でも成功する。**「送ったのに誰も見ていない」が正常系**になる
- 設定は `stage` と `panel` の両方に配信する（`stage` は locale のために必要）

**得るもの**
- 記憶 UI を置ける。**ホバーで消えない操作面**ができる
- キャラクターの接続から inspector の毎ターン送信が消える
- ウィンドウを増やすときの型が決まる（Phase 4a の権限プロンプト、Phase 7 の Widget）

## Consequences

- [architecture/ui.md](../architecture/ui.md) のウィンドウ一覧に `inspector` / `memory` が入り、
  §5 の「独立ウィンドウにしない」は**この ADR が置き換える**
- [contracts/wire.json](../contracts/wire.json) に `panel` の namespace / methods / inbound、
  `window_labels`、`tauri_commands` が増える
- [contracts/security-boundaries.md](../contracts/security-boundaries.md) B2 に `panel` の行が要る。
  **panel は `os.*` に到達できない**（namespace が違う）
- **Invariant 8 との関係**: panel ウィンドウは Lumi 自身のウィンドウであり、
  `WindowKind::is_protected()` は全ウィンドウに対して true を返す。
  記憶 UI は `user_confirmed` への昇格経路（Invariant 7）なので、
  **Lumi 自身がそのボタンを押せないことが必要**であり、それは既に満たされている
- **記憶の消去・エクスポートは `panel.*` の inbound として入る** → [contracts/privacy.md](../contracts/privacy.md) §5
