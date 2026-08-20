# ADR-028: Stage → Core の要求方向を作る（`request`）

> **[ADR-031](ADR-031-request-side-effects.md) が受理条件4の文言を修正した。**
> 「副作用を持つ操作を含まないこと」は「**Tool を要する副作用**を含まないこと」と読む。
> Core が所有する状態（設定）の変更はこの経路で許される。

Status: Accepted
Date: 2026-08-17

| 関連 | |
|---|---|
| 修正する記述 | `core/lumi/transport/protocol.py`「クライアントからの `command` は決して受理しない」（Phase 0） |
| 制約 | [contracts/invariants.md](../contracts/invariants.md) Invariant 1（Authority）, 2（Tool Gate） |
| 境界 | [contracts/security-boundaries.md](../contracts/security-boundaries.md) B2 |
| 設計 | [architecture/core.md](../architecture/core.md) §3, §6b（設定） |
| 実装 | `core/lumi/transport/protocol.py`, `core/lumi/transport/server.py`, `stage/src/core/connection.ts` |

## Decision

**Stage → Core の要求方向を作る。** フレームの種別は **`request`** とし、Core は既存の
`result` 形式で応える。

```
Core → Stage:  kind = "command"   （Core が決め、Stage は従う / 答える）
Stage → Core:  kind = "request"   ★ 新設（Stage は問い、Core が決める）
Core → Stage:  kind = "result"    （request への応答）
```

**`command` を双方向に使い回さない。** 名前が同じだと、コードでもログでも
「どちらが決めたのか」が読めなくなる。**非対称であることを名前で保つ。**

### 受理の条件 — 4つすべてを満たすときだけ

| # | 条件 | 実装 |
|---|---|---|
| 1 | `stage.*` namespace であること | `method_matches_role`（既存） |
| 2 | **Core が明示的に登録した method であること** | 登録レジストリ。未登録は `unknown_method` |
| 3 | ハンドラが payload を検証すること | 各ハンドラの責任 |
| 4 | **副作用を持つ操作を含まないこと** | 下記 |

**2 が本体である。** `stage.*` のすべてが Stage から起動してよいわけではない
（`stage.setup.prompt` は Core → Stage 専用である）。**登録しない限り届かない**という
構造にすることで、経路の追加が常に明示的な行為になる。

### この経路で**やってはいけないこと**

- **Tool を呼ばない。** 副作用を持つ操作は例外なく `ToolRegistry.invoke` → Permission Kernel を通る（**Invariant 2**）。`request` はそのバイパス経路になってはならない
- **`trust_level` を上げない。** 昇格は2箇所だけ（**Invariant 7**）
- **Activity / Tool の状態遷移を起こさない。** Arbiter と Registry の独占（**Invariant 4**）

Phase 1 でこの経路に載るのは **Core が所有する設定の変更だけ**である。

## Reason

### 設定 UI が原理的に作れなかった

[architecture/core.md](../architecture/core.md) §6b で設定の保存形式を決めたが、
**ユーザーが設定を変える経路が無い。** 現在の WS はクライアントから `hello` と `result`
しか受理しないため、設定 UI は読み取り専用にしかならない。

**押しても何も起きない操作子を置くのは、機能が無いことより悪い。**

### 「経路が無いこと」で保証していたものは、実は別のもの

Phase 0 の記述は「その経路が無いこと自体で、決定の起点が Core であることを保証する」と書いた。
**これは保証の手段であって、保証したい内容ではない。**

守りたいのは Invariant 1 の **「権限の最終決定者は Core Kernel だけ」** である。
そして **ユーザーの操作は Stage の判断ではない。**
Stage は**ユーザーの意思を中継しているだけ**であり、これは
`stage.setup.prompt` で既に起きていること（Stage が「取得する」を返す）と同じである。

**違うのは、どちらが会話を始めるかだけ。** 起点が Stage であっても、
**受理するか・何をするかを決めるのは Core**であり、Invariant 1 は破れていない。

### B2 は元から双方向を前提にしている

[security-boundaries.md](../contracts/security-boundaries.md) B2 は
「**`stage.*` namespace のみ受理**。`os.*` / `ext.*` は Stage から受け付けない」と書いている。
**「Stage から受け付ける」ことは前提にされており、制限されているのは namespace である。**

Widget Broker の経路（`Widget iframe → postMessage → Widget Broker (Stage内) → Core`、
同 §Widget）も同じ方向を前提にしている。**Phase 7 で必ず必要になる方向を、
Phase 1 で必要になった時点で作る。**

## Alternatives

### 1. Core が定期的に「設定を変えるか」と聞く

`stage.setup.prompt` と同じ形にする。**新しい方向を作らずに済む**のが利点。

採らない理由: **ユーザーが設定を開いた瞬間に Core が聞きに来る保証が無い。**
ポーリングすれば作れるが、それは要求方向をポーリングで模倣しているだけであり、
遅延と複雑さが増えるだけで境界は 1mm も安全にならない。

### 2. 設定ファイルを Stage が直接書く

Stage は WebView なのでファイル書き込みには Shell（`os.*`）が要る。
**`stage.*` が OS 特権を要求しない**という規則（[core.md](../architecture/core.md) §3）に
真正面から反する。**論外。**

### 3. `command` を双方向に使う

フレーム種別を増やさずに済む。**利点は小ささだけ。**

採らない理由: **ログとコードで方向が読めなくなる。** 「この `command` はどちらが出したのか」を
毎回 corr_id の出所から辿ることになる。境界の設計で最も避けたいのは
**「読めば分かるが、読まないと間違える」構造**である。

### 4. 設定は環境変数と設定ファイルだけにする（UI から変えない）

**Phase 1 の現状がこれ**であり、動いてはいる。

採らない理由: [ADR-019](ADR-019-tts-engine-distribution.md) 原則2（選択肢を対等に見せる）と
同じ精神で、**設定は「見せるが触らせない」ものではない。**
デスクトップに常駐するものの設定を、毎回テキストエディタで開かせるのは無理がある。

## Trade-offs

### 受け入れるコスト

- **攻撃面が増える。** Stage が乗っ取られた場合、登録済みの method を呼べる。
  Phase 1 では設定3項目（`inference_device` / `llm_model` / `stt_model`）の変更にとどまる
- **「Stage が乗っ取られてもできるのは変な表情と変な吹き出しまで」という表現が正確でなくなる。**
  正確には「**変な表情・変な吹き出し・登録済み method の範囲**」になる。
  → [security-boundaries.md](../contracts/security-boundaries.md) を更新する
- レジストリという概念が1つ増える

### 得るもの

- 設定 UI が成立する
- **Phase 7 の Widget Broker が乗る経路が先に用意される**（後から入れると Broker の設計が変わる）
- 「Stage から来る要求は Core が検証して決める」という形が、**1回だけ正しく作られる**

### 保証しないこと

- **Stage が乗っ取られていないことは保証しない。** この経路の安全性は
  「**登録した method の危険度の上限**」でしか保証されない。危険な操作を登録すれば危険になる
- **payload の妥当性は transport が保証しない。** 各ハンドラが検証する

## Consequences

1. `parse_client_message` が `request` を受理する。**`command` は依然として受理しない**
2. `WsServer` に inbound method のレジストリが要る。**未登録は `unknown_method` で拒否**（fail-closed）
3. Stage 側に `request(method, payload)` が要る（応答を待つ Promise）
4. [security-boundaries.md](../contracts/security-boundaries.md) B2 の記述を更新する
5. `docs/contracts/wire.json` に inbound method の一覧が要る。**3言語のテストで固定する**
6. **この経路に Tool 実行を載せるときは、必ず新しい ADR を書く。**
   Invariant 2 のバイパスは、1回でも作れば戻せない
