# ADR-010: Signal と DomainEvent を分離し、per-stream ordering を EventBus が保証する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../contracts/event-model.md](../contracts/event-model.md), [../contracts/authority-matrix.md](../contracts/authority-matrix.md) |

---

## Decision

**3つの決定を1つの ADR にまとめる**（互いに依存するため）。

### 1. `Signal` と `DomainEvent` を別の型にする

| | **Signal**（inbound） | **DomainEvent**（Core内部） |
|---|---|---|
| 発行者 | Shell / Stage / Extension / Widget | **Core Kernel のみ** |
| `stream_key` / `sequence_id` | **持たない**（型で保証） | 持つ |
| 永続化 | しない | する |
| Hook 起動 | しない | する |

### 2. 順序保証は per-stream ordering

- 同一 `stream_key` 内では順序を保証する
- 異なる `stream_key` 間では保証しない
- 消費側は欠番・逆転を検出したらエラーにする

### 3. 採番は EventBus が独占する

- 発行者は `sequence_id` を持たない draft を publish する
- EventBus が `stream_key` ごとに直列化して採番する
- **採番と永続化は同一トランザクション**

---

## Reason

### 1: 外部が DomainEvent を書けると Invariant 6 が壊れる

「外から来た通知」と「Lumi の世界で起きた事実」を同じ型にすると、**Core が解釈していない状態変更が履歴に残る**。

```
Sensor Ext が直接 DomainEvent("WorldFacetChanged") を書く
  → Core は何が起きたか知らない
  → 監査もできない
  → Invariant 6（No Hidden Authority）違反
```

正しい流れ:

```
Sensor Ext → Signal("sensor.foreground_app")
  → Core が認証・schema検証・capability検査
  → Core が WorldFacet を更新
  → Core が DomainEvent("WorldFacetChanged") を発行
```

**Signal は「素材」であり、DomainEvent にするかどうか・どう表現するかは Core が決める。**

### 1 の副次的な利点

| 利点 | 内容 |
|---|---|
| 採番責任と整合 | 外部に `sequence_id` を渡す必要がなくなる |
| Provenance と整合 | Signal が `trust_level` を持ち、派生する DomainEvent に伝播する |
| Authority Matrix と整合 | DomainEvent 発行列に ✓ が立つのは Core Kernel だけ、という表がそのまま型になる |

### 2: 「順序保証しない」は誤りだった

`ActivityStarted` と `ActivityEnded` の順序が逆転したら、監査も Memory も壊れる。

かといって全 Event の全順序を保証するのはコストが高すぎる（並行処理が全部直列化される）。

**per-stream ordering が正解。** stream 内では厳密、stream 間では自由。

### 2: なぜ Ordered/Unordered のクラス分けにしないのか

「この Event は順序保証が要るか?」という判断は**間違えやすい**。

`stream_key` は「何についての Event か」から自然に決まるため、間違えにくい。「Activity についての Event なら `activity:<id>`」と機械的に決まる。

### 3: 複数コルーチンが同じ stream に発行する状況は必ず起きる

発行者側で採番すると、競合して重複や欠番が生まれる。

**EventBus が唯一の採番者**とし、`stream_key` ごとに直列化する。

### 3: 採番と永続化を同一トランザクションにする理由

分けると、その間にクラッシュした場合にギャップが生まれる。

**同一トランザクションにすることで、ギャップは「真の異常」を意味する**ようになり、検出が意味を持つ。

---

## Alternatives

### A. 単一の Event 型（当初案）

**利点:** 型が1つで単純
**欠点:** 外部が DomainEvent を書ける。`sequence_id` を外部に渡す必要が出る。Invariant 6 が型で守られない

### B. 全 Event の全順序を保証する

**利点:** 考えることが減る
**欠点:** 並行処理が全部直列化される。スループットが出ない

### C. 順序を全く保証しない（当初案）

**利点:** 実装が最も単純
**欠点:** **`ActivityStarted` / `ActivityEnded` の逆転で監査と Memory が壊れる**

### D. Ordered / Unordered の2クラスに分ける

**利点:** 必要なところだけ保証する
**欠点:** 「これはどっちだ?」の判断ミスが起きる。判断が実装者に委ねられる

### E. 発行者が採番する

**利点:** EventBus のロックが不要
**欠点:** 複数コルーチンで競合。重複・欠番が生まれる

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 |
|---|---|
| 型が2つになる | Signal と DomainEvent、加えて `DomainEventDraft` |
| 昇格ロジックが要る | Signal → DomainEvent の変換を Core が書く |
| per-stream ロック | `stream_key` ごとの直列化コスト |
| stream_key の設計が必要 | 何を1つの stream とするかを決める |

### 得るもの

- **Invariant 6 が型で保証される**（外部が DomainEvent を作れない）
- 採番の競合が構造的に起きない
- 必要な順序だけを保証し、不要な直列化を避ける
- ギャップ検出が意味を持つ

---

## Consequences

### `DomainEventDraft` という型が必要になる

```python
@dataclass(frozen=True)
class DomainEventDraft:
    stream_key: str
    type: str
    payload: dict
    causation_id: str | None
    correlation_id: str
    # sequence_id を持たない
```

**発行者が `sequence_id` を触れないことを型で保証する。**

### stream_key の設計

```
activity:<activity_id>      ActivityStarted / ToolCalled / ActivityEnded
memory:<subject>            MemoryFormed / MemorySuperseded / MemoryArchived
world:<facet_key>           WorldFacetChanged
session:<session_id>        SessionStarted / UserSpoke / LumiSpoke / SessionEnded
autonomy                    DriveThresholdReached / AutonomyFeedbackReceived
permission                  PermissionAsked / PermissionGranted / PermissionDenied
extension:<ext_id>          ExtensionLoaded / ExtensionFailed / ExtensionUnloaded
```

### Extension プロトコルに DomainEvent の経路を作らない

`ext.*` で Extension が送れるのは Signal と Response のみ。**静的検査で保証する。**

### 単一 DB であることが効く

採番と永続化を同一トランザクションにするには、Event ストアがトランザクションを共有できる必要がある。

**SQLite 単一ファイル（[ADR-004](ADR-004-sqlite-vec-memory.md)）がこれを可能にしている。** 別プロセスのベクトル DB を使っていたら、この保証は得られなかった。

### AIRI の反面教師

AIRI は約60種の WS イベントでモジュールのライフサイクルを振り付けている。

```
module:authenticate → authenticated → registry:modules:sync → module:announce
→ prepared → configuration:{validate,plan,commit} → configured
→ contribute:capability:offer → activated → status(ready) → status:change
```

**これは追跡不能である。** どこで止まったか、なぜ進まないかが分からない。

**Lumi はライフサイクルを明示的な Command シーケンスにする。** Event は「起きた事実の記録」であって、制御フローではない。

> **不変条件: 制御フローが DomainEvent の観測に依存してはならない。** 結果が要るなら Command を使う。

### Command / Signal / DomainEvent の判断基準

```
結果が必要か？
  Yes → Command
  No  ↓
外から来たか？
  Yes → Signal
  No  → DomainEvent
```

**迷ったら Command にする。** あとから Event に緩めるのは簡単だが、Event で組んだ制御フローを Command に直すのは難しい。
