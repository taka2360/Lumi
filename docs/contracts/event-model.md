# Event Model — Signal と DomainEvent、Command との分離

> **Status: Confirmed**
> **外から来るものと、Core が「起こった」と宣言するものは、同じ型であってはならない。**

親: [DESIGN.md](../DESIGN.md) / 関連: [invariants.md](invariants.md), [authority-matrix.md](authority-matrix.md)

---

## 3つの概念

| | **Command** | **Signal** | **DomainEvent** |
|---|---|---|---|
| 意味 | 「実行せよ」 | 「外部からこう通知された」 | 「Lumi の世界でこれが起きた」 |
| 時制 | 命令形 | 現在形 | **過去形** |
| 発行者 | Core 内部 | Shell / Stage / Extension / Widget | **Core Kernel のみ** |
| 宛先 | 単一ハンドラ | Core の受信境界 | fan-out |
| 戻り値 | **あり。失敗しうる。await する** | なし | なし |
| 順序 | 保証。backpressure あり | 保証しない | **per-stream で保証** |
| 永続化 | しない | **しない**（監査には残す） | **する** |
| Hook 起動 | — | しない | する |
| 例 | `Speak`, `ExecuteTool`, `OpenWidget` | `sensor.foreground_app`, `ui.user_said_noisy` | `UserSpoke`, `ToolCompleted`, `MemoryFormed` |

---

## 不変条件

> **制御フローが DomainEvent の観測に依存してはならない。** 結果が要るなら Command を使う。

AIRI は約60種の WS イベントでモジュールのライフサイクルを振り付けている（`module:authenticate → authenticated → registry:modules:sync → module:announce → prepared → configuration:* → configured → contribute:capability:offer → activated → status(ready)`）。これは**追跡不能**であり、どこで止まったか、なぜ進まないかが分からなくなる。

**Lumi はライフサイクルを明示的な Command シーケンスにする。**

---

## Signal と DomainEvent の型

```python
@dataclass(frozen=True)
class Signal:
    """外部から Core に届く通知。"""
    source_id: PeerIdentity      # 誰が送ったか
    type: str
    payload: dict
    received_at: datetime
    trust_level: TrustLevel      # 送出元の信頼度から決まる

    # stream_key も sequence_id も持たない（型で保証）


@dataclass(frozen=True)
class DomainEvent:
    """Core が「起きた」と宣言する事実。"""
    id: EventId
    stream_key: str              # 順序保証の単位
    sequence_id: int             # stream_key 内での単調増加。ギャップなし
    causation_id: str | None     # これを引き起こした Command / Signal / DomainEvent
    correlation_id: str          # 一連の処理の追跡ID
    type: str
    payload: dict
    occurred_at: datetime
```

**`Signal` が `stream_key` / `sequence_id` を持たないことを型で保証する。** これにより「外部が DomainEvent を直接書く」経路がコンパイル時に塞がる。

---

## 昇格の流れ

```
Signal 受信
  ↓
送出元の認証（peer identity / WS token）
  ↓
schema 検証
  ↓
capability 検査（この送出元はこの Signal を送ってよいか）
  ↓
Core が意味を解釈する
  ↓
（必要なら）状態を変更し、DomainEvent を発行する
```

> **重要: 外部は DomainEvent の内容を直接決められない。**
> Signal は「素材」であり、DomainEvent にするかどうか・どう表現するかは Core が決める。

### 例1: Sensor Extension

```
Sensor Ext
  → Signal(type="sensor.foreground_app", payload={"app": "factorio.exe"})
  → Core が認証・schema検証・capability検査
  → Core が WorldFacet("user.focus_app") を更新
  → Core が DomainEvent(
        stream_key="world:user.focus_app",
        type="WorldFacetChanged",
        causation_id=<signal id>
    ) を発行
```

Sensor は World facet を直接書かない（[authority-matrix.md](authority-matrix.md) の責務行列）。

### 例2: 「うるさい」ボタン

```
Stage
  → Signal(type="ui.user_said_noisy")
  → Core が検証
  → Core が AutonomyBudget を消費 + Drive を強制減衰 + Memory に書き込み
  → Core が DomainEvent(stream_key="autonomy", type="AutonomyFeedbackReceived") を発行
```

Stage は「予算を減らせ」とは言わない。「ユーザーがうるさいと言った」と伝えるだけ。**何をするかは Core が決める。**

### 例3: Tool の完了

```
Capability Ext
  → Response(tool result)          ← これは Signal でもない。Command の戻り値
  → Core が ToolResult を構築（provenance 付与）
  → Core が DomainEvent(stream_key="activity:<id>", type="ToolCompleted") を発行
```

ツールの結果は **Command の戻り値**であって Signal ではない。await できるものは Command。

---

## この分離が必要な4つの理由

| 理由 | 説明 |
|---|---|
| **Invariant 6 と整合** | 外部が直接 DomainEvent を書けると、Core が解釈していない状態変更が履歴に残る |
| **採番責任と整合** | 外部に `sequence_id` を渡す必要がなくなり、「EventBus が唯一の採番者」が自然に守られる |
| **Provenance と整合** | Signal が `trust_level` を持ち、そこから派生する DomainEvent / ContextBlock に伝播する |
| **Authority Matrix と整合** | DomainEvent 発行列に ✓ が立つのは Core Kernel だけ、という表がそのまま型になる |

---

## 順序保証

**DomainEvent のみ。Signal には順序保証がない。**

### 契約

- **同一 `stream_key` 内では順序を保証する**（配送も処理も）
- **異なる `stream_key` 間では順序を保証しない**
- 消費側は `sequence_id` の欠番・逆転を検出したら**エラーにする**（黙って処理しない）
- **購読者は、いま処理している `stream_key` へ publish してはならない**（→ [ADR-030](../decisions/ADR-030-per-stream-dispatch.md)）

「配送も処理も」を満たすには、**配送が終わるまで次の採番を始めてはならない。**
配送をロックの外に出すと、`await` する購読者が次のイベントに追い越される。

**購読者が処理中の stream へ publish できないのはこの帰結である。** そこで発行される
イベントは定義上「処理中のイベントの後ろ」に順序づけられるが、その処理はまだ終わっていない。
**順序を与えられない。** 実装は待たせずに例外を投げる（待たせれば無言のデッドロックになり、
「黙って劣化しない」に反する）。**別の `stream_key` への publish は自由。**

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

`ActivityStarted` と `ActivityEnded` は同じ `stream_key = activity:<id>` に属するため、順序が保証される。

### なぜ Ordered/Unordered のクラス分けにしないのか

「この Event は順序保証が要るか?」という判断は間違えやすい。

`stream_key` は**「何についての Event か」から自然に決まる**ため、間違えにくい。「Activity についての Event なら `activity:<id>`」と機械的に決まる。

---

## 採番責任 — EventBus が唯一の採番者

複数のコルーチンが同じ stream に発行する状況は**必ず発生する**。

### 規則

1. **発行者は `sequence_id` を持たない DomainEvent を publish する。** 発行者側で採番してはならない
2. **EventBus が `stream_key` ごとに直列化して採番する**（per-stream の単一 writer）
3. **採番と永続化は同一トランザクション**で行う
4. ギャップを検出したら、欠落を無視せず**異常として記録する**

### 3 が重要な理由

採番と永続化を分けると、その間にクラッシュした場合にギャップが生まれる。同一トランザクションにすることで、**ギャップは「真の異常」を意味する**ようになり、検出が意味を持つ。

### 実装イメージ

```python
class EventBus:
    async def publish(self, draft: DomainEventDraft) -> DomainEvent:
        """draft は sequence_id を持たない。"""
        self._refuse_reentry(draft.stream_key)        # 処理中の stream へは publish できない
        async with self._lock_for(draft.stream_key):
            async with self._store.transaction() as tx:
                seq = await tx.next_sequence(draft.stream_key)
                event = DomainEvent(sequence_id=seq, **draft.fields())
                await tx.append(event)
            await self._dispatch(event)   # 永続化後に配送。**ロック内**（→ ADR-030）
        return event
```

`DomainEventDraft` と `DomainEvent` を別の型にすることで、**発行者が `sequence_id` を触れないことを型で保証する**。

---

## Command

```python
@dataclass(frozen=True)
class Command:
    id: CommandId
    type: str
    payload: dict
    correlation_id: str
    idempotency_key: str | None   # 副作用を持つ Command には必須（recovery.md）
```

### 規則

- 単一ハンドラに配送される
- **戻り値を持ち、失敗しうる**。呼び出し側は await する
- 順序が保証され、backpressure がある
- 副作用を持つ Command（write / send / delete / launch / input）には `idempotency_key` が必須

### Command か Signal か DomainEvent か、の判断

```
結果が必要か？
  Yes → Command
  No  ↓
外から来たか？
  Yes → Signal
  No  → DomainEvent（Core が発行）
```

迷ったら **Command にする**。あとから Event に緩めるのは簡単だが、Event で組んだ制御フローを Command に直すのは難しい。

---

## Hook

> **この節が Hook 一覧の唯一の定義場所。** 他のドキュメントはここへリンクする。

Hook は DomainEvent とは別の仕組み。**同期的・順序保証あり・veto 可能**。

固定セットのみ（乱用しない）:

| Hook | veto 可 |
|---|---|
| `before_llm` / `after_llm` | — |
| `before_tool` / `after_tool` | **before_tool は veto 可** |
| `before_speak` / `after_speak` | — |
| `on_memory_write` | — |
| `on_activity_start` / `on_activity_end` | — |
| `on_app_start` / `on_app_shutdown` | — |

**Hook は観測と拒否はできるが、任意の状態書き換えはできない**（Invariant 6）。

戻り値は `Continue` か `Veto(reason)` のみ。状態を返して Core がそれを適用する、という設計にはしない。

**このセットを増やすには ADR を要求する。**

---

## テスト

| # | テスト |
|---|---|
| 1 | `Signal` 型が `stream_key` / `sequence_id` を持たない（型検査） |
| 2 | Signal が DomainEvent に昇格せずに永続化されない |
| 3 | 同一 `stream_key` 内で `sequence_id` が単調増加しギャップが無い |
| 4 | 複数コルーチンから同一 stream に並行 publish しても欠番・重複が発生しない |
| 5 | 消費側が欠番・逆転を検出してエラーにする |
| 6 | `sequence_id` の代入が `EventBus` 以外に存在しない（静的検査） |
| 7 | Extension プロトコルに `DomainEvent` を送る経路が無い（静的検査） |
| 8 | 採番後・配送前にクラッシュしても永続化済みであること |
| 9 | `ActivityStarted` / `ActivityEnded` の順序が保証される |
| 9b | **遅い購読者が居ても、同一 stream の配送順が入れ替わらない**（購読者が `await` している間に次の publish が追い越さない → [ADR-030](../decisions/ADR-030-per-stream-dispatch.md)） |
| 9c | **購読者が処理中の stream へ publish すると、待たされずに例外になる**（無言のデッドロックにしない） |
| 10 | `before_tool` Hook の veto がツール実行を止める |
