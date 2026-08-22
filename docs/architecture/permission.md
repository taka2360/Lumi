# Permission Architecture

> **絶対原則: 権限判断は Core だけが行う。Extension・Stage・Shell・Tool・LLM は判断しない。**（Invariant 1, 2）

親: [DESIGN.md](../DESIGN.md) / 関連: [../contracts/tool-execution.md](../contracts/tool-execution.md), [../contracts/security-boundaries.md](../contracts/security-boundaries.md), [ADR-006](../decisions/ADR-006-kernel-execution-contract.md)

---

## 1. PermissionSpec — Tool の静的宣言

```python
@dataclass(frozen=True)
class PermissionSpec:
    capability: str              # "fs.read" | "fs.write" | "browser.navigate"
                                 # | "shell.exec" | "input.inject" | ...
    risk: Risk                   # L0..L4
    reversible: bool
    side_effect: SideEffect      # none | local | external | irreversible
    cancellation: Cancellation   # cooperative | hard | non_cancellable
```

**Tool はこれを宣言するだけ。判断も正規化も検証もしない。** → [../contracts/tool-execution.md](../contracts/tool-execution.md)

`lane`（どの Canonicalizer / 検証器を使うか、および Class A / B のどちらか）は `PermissionSpec` ではなく **`Tool` 自身に持たせる**。lane は実行機構の選択であって権限の宣言ではないため。

---

## 2. Policy — 唯一の定義

> **Policy はこの節の `decide()` が唯一の定義である。**
> 以降の表はすべて `decide()` の**説明**であって、実装の根拠ではない。表と散文が食い違ったら `decide()` が正。

```python
def decide(
    base_risk: Risk,              # Tool の PermissionSpec.risk（宣言値）
    actor: Actor,
    effective_trust: TrustLevel,  # 呼び出し元 context の join（provenance.md）
    grant: Grant | None,          # scope に一致する有効な Grant
) -> Decision:
    """Policy の唯一の定義。純粋関数であること。

    引数はこの4つだけ。LLM の理由文・Tool の自己申告・Extension の reason は
    引数に含まれない（Invariant 1, 3）。
    """

    # ── 1. 実効リスクを決める。actor による昇格はここで1回だけ起きる ──
    effective_risk = base_risk
    if actor is Actor.SELF_INITIATED:
        effective_risk = escalate_for_self_initiated(base_risk)
    # scheduled は user_initiated と同じ扱い、system は L0 のみ（後述）

    # ── 2. 以降の規則はすべて effective_risk に対して適用する ──
    if actor is Actor.SYSTEM and base_risk > Risk.L0:
        return Decision.DENY                      # Job / idle は L0 のみ

    if effective_risk is Risk.DENIED:
        return Decision.DENY                      # self_initiated の L3 以上

    if effective_trust is TrustLevel.TAINTED and effective_risk >= Risk.L3:
        return Decision.ASK                       # provenance 昇格

    if effective_risk >= Risk.L4:
        return Decision.ASK                       # L4 は Grant があっても毎回聞く

    if effective_risk >= Risk.L2 and grant is None:
        return Decision.ASK

    return Decision.ALLOW


def escalate_for_self_initiated(base: Risk) -> Risk:
    """自律行動の実効リスク。L3 以上は「昇格」ではなく拒否になる。"""
    return {
        Risk.L0: Risk.L0,     # 読み取り・観測は自律でも許す
        Risk.L1: Risk.L1,     # ブラウザ閲覧は許す（AutonomyBudget が別途効く）
        Risk.L2: Risk.L3,     # 実効的に L3 = ask
        Risk.L3: Risk.DENIED,
        Risk.L4: Risk.DENIED,
    }[base]
```

### 「1段上がる」という説明を捨てた理由

当初は「`self_initiated` なら要求レベルが1段上がる」と書いていたが、**L3 で表と矛盾する**。

| | 表の値 | 「1段上げる」の帰結 |
|---|---|---|
| L2 self | ask | L3 の user 列 = ask ✓ |
| **L3 self** | **deny** | L4 の user 列 = **ask** ✗ |

`escalate_for_self_initiated` を明示的な写像として書くことで、この曖昧さを消す。**自律行動の L3 以上は「厳しくなる」のではなく「できない」。**

### 規則の適用順序を固定する理由

「累積適用され、最も厳しいものが勝つ」だけでは、**provenance 昇格が `base_risk` を見るのか `effective_risk` を見るのか**が決まらない。

例: `base_risk = L2` + `self_initiated` + `TAINTED`

- `base_risk` を見る → provenance 規則は不発火（L2 < L3）
- `effective_risk` を見る → 発火して `ask`

**`decide()` は effective_risk を見る**（＝ actor 昇格を先に適用する）。実装が割れないよう、順序を関数の行順として固定する。

---

## 3. 説明用の表

以下は `decide()` の挙動を人間向けに表にしたもの。**実装はこの表からではなく `decide()` から起こす。**

| L | 内容 | `user_initiated` / `scheduled` | `self_initiated` | `system` |
|---|---|---|---|---|
| **L0** | 読み取り・観測（screenshot, world 読み） | allow | allow | allow |
| **L1** | ブラウザ閲覧・Web検索・YouTube | allow | allow（予算内） | deny |
| **L2** | ファイル読み取り・作業領域への書き込み | allow（Grant 済み scope）/ ask | **ask** | deny |
| **L3** | アプリ起動・入力インジェクション・任意パス書き込み | ask | **deny** | deny |
| **L4** | シェル実行・削除・外部送信・不可逆操作 | **ask（Grant があっても毎回）** | **deny** | deny |

`effective_trust == TAINTED` のときは、上表の実効 L3 以上がすべて `ask` になる（`allow` は残らない）。

〔L の割り当ては Provisional。L2/L3 の境界は使ってみないと分からない。`policy_version` で追跡する〕

### 設計の核 — `actor` が判断に入る

「ユーザーが頼んだファイル読み取り」と「Lumi が勝手にするファイル読み取り」は**別の行為である**。当たり前のことを、コードで表現する。

AIRI にはこの区別が存在しない。tool call はすべて同じ経路（そもそも承認自体が無い）。

### Actor の種類

| actor | 意味 | 実効 |
|---|---|---|
| `user_initiated` | ユーザーの発話・操作が直接のきっかけ | 基準 |
| `self_initiated` | Drive System から発火した自律行動 | `escalate_for_self_initiated` |
| `scheduled` | ユーザーが登録したタスク・リマインダ | `user_initiated` と同じ |
| `system` | idle Activity、および Job（[ADR-018](../decisions/ADR-018-foreground-and-jobs.md)） | **L0 のみ** |

---

## 3.1 `decide()` の外にある規則

`decide()` は実行時の判断だけを行う。以下は**登録時**または**Kernel 実行契約の側**で強制される。

| # | 規則 | いつ | 効果 |
|---|---|---|---|
| 1 | **cancellation 制約** | 登録時 | `non_cancellable` かつ `side_effect != none` の Tool は `risk < L3` で**登録できない** |
| 2 | **Class B 制約** | 登録時 | out-of-process の Tool は `side_effect != none` なら `risk < L3` で**登録できない**（[ADR-017](../decisions/ADR-017-out-of-process-tool-contract.md)） |
| 3 | **ハードコード不変条件** | canonicalize 時 | §6 の対象は scope 生成の段階で `deny`。`decide()` に到達しない |

**これらを `decide()` の中に書かない。** 実行時に毎回判定する必要が無く、登録時に落とす方が fail-closed として強い。

### provenance 昇格の意味

プロンプトに untrusted 由来のデータが1つでも混ざっていたら、実効 L3 以上のツールは必ずユーザーに聞く。

これにより「Web ページを読んだあと、その内容に誘導されてファイルを消す」が防げる。時間経過や記憶経由の伝播でも効く（[../contracts/provenance.md](../contracts/provenance.md)）。

---

## 4. Scope の正規化

**Policy は `SecurityScope` に対してのみ適用される。生の引数には適用しない。**

```
Raw Input
   ↓  Canonicalizer[lane]   ← Kernel 所有
SecurityScope (immutable)
   ↓  Policy.decide
Decision
```

詳細と lane 別の実装 → [../contracts/tool-execution.md](../contracts/tool-execution.md)

### fail-closed

**正規化に失敗した入力は `deny`。** 「よく分からないので通す」経路を作らない。

---

## 5. Grant — スコープ付きトークン

**ブール値ではない。**

```python
@dataclass
class Grant:
    id: GrantId
    capability: str
    security_scope: SecurityScope    # 正規化済み
    expires_at: datetime | None
    remaining_uses: int | None
    granted_at: datetime
    granted_by: Literal["user"]      # user 以外に値がない
```

### これで表現できること

- 「このセッション中、`C:\Users\yuasa\Projects` 以下は読んでいい」
- 「この URL に1回だけアクセスしていい」
- 「今日いっぱい、このフォルダに書いていい」

### UI の規則

> **「全部許可」という選択肢を UI に置かない。**

置いた瞬間に、ユーザーは必ずそれを選ぶ。選択肢は常に「今回だけ」「このスコープで、この期間」「拒否」の3つ程度に留める。

### 消費

Grant は `remaining_uses` を減算する形でのみ変更される。**Tool から Grant を作ることも消すこともできない**（[../contracts/authority-matrix.md](../contracts/authority-matrix.md)）。

---

## 6. ハードコードされた不変条件

Policy に例外なく組み込む。設定で無効化できない。

| # | 内容 |
|---|---|
| 1 | **Lumi 自身のウィンドウ・プロセス・設定ファイル・監査ログは、いかなるツールからも操作対象にならない** |
| 2 | **権限プロンプトウィンドウは入力インジェクション・スクリーンキャプチャの対象にならない**（Invariant 8。Shell 側でも二重に拒否） |
| 3 | 認証情報ストア（Windows Credential Manager 等）はデフォルト deny |
| 4 | ブラウザプロファイルディレクトリはデフォルト deny |
| 5 | SSH 鍵・GPG 鍵のディレクトリはデフォルト deny |
| 6 | `audit_log` への書き込み経路が filesystem tool から到達不能 |

**1 は AIRI の `denyApps` に自分自身が入っている設計と同じ発想であり、正しい。**

---

## 7. 監査ログ

### 記録内容 — 決定内容にかかわらず全件

```sql
audit_log (
  id, ts, actor, activity_id, correlation_id,
  capability, security_scope_json, raw_input_digest,
  decision, reason,
  policy_version, policy_rule_id,        -- 必須
  grant_id,
  tool, args_digest, result_digest,
  provenance_class, trust_level,
  prev_hash, record_hash                 -- Phase 4a
)
```

### `policy_version` / `policy_rule_id` が必須な理由

Policy は将来変わる（例: 2026-08 は L3=ask、2027-02 は L3=deny）。
そのとき「なぜこの操作を許可したのか」に答えるには、**当時のルールが分からなければならない**。

### `raw_input_digest` と `security_scope` の両方を記録する理由

正規化前と後の両方を残すことで、「正規化が正しかったか」を後から検証できる。ダイジェストにするのは、機密情報をログに残さないため。

### 保持期間

**既定 180 日。** 唯一の定義場所は [../contracts/privacy.md](../contracts/privacy.md) §2。
append-only であることと、無限に貯め続けることは別である。

### append-only の正確な意味

**定義と Phase ごとの実装は [../contracts/security-boundaries.md](../contracts/security-boundaries.md) が唯一の定義場所。**

要点のみ: **「Lumi の全 Tool 経路から改竄・削除できない」という意味である。** OS 管理者権限を持つ別プロセスからの改竄は防げない。**それは脅威モデル外であり、防げると書かない。**

---

## 8. 権限プロンプト UI

**独立ウィンドウ。** Invariant 8 の保護対象。

### 表示すべき内容

| 項目 | 例 |
|---|---|
| 何をしようとしているか | 「ファイルを読もうとしています」 |
| **対象（正規化後）** | `C:\Users\yuasa\Projects\Lumi\README.md` |
| なぜ（Lumi の説明） | 「さっき話していたプロジェクトの内容を確認したくて」 |
| **actor** | 「あなたが頼んだ操作です」/ **「Lumi が自分で判断した操作です」** |
| **provenance 警告** | 「直前に外部サイトの内容を読んでいます」（tainted のとき） |
| リスク | L3 / 不可逆 など |

### `actor` と provenance を必ず見せる理由

ユーザーが「これは自分が頼んだことだったか?」を判断できないと、承認の意味がない。
特に **`self_initiated` + `tainted` の組み合わせは最も危険**であり、それが視覚的に分かる必要がある。

### Lumi の説明を信用しない

「なぜ」の欄には LLM が生成した説明を表示するが、**Policy はこの説明を見ない**（Invariant 1）。説明はユーザーの判断材料であって、判断の根拠ではない。

---

## 9. Extension の権限

**実効権限 = `manifest ceiling ∩ policy ∩ user grant`**（Invariant 5）

```
manifest ceiling : Extension が「これだけ使いたい」と宣言した上限
policy           : Lumi のポリシーが許す範囲
user grant       : ユーザーが実際に承認した範囲
```

### AIRI の欠陥を繰り返さない

AIRI は交差モデル自体は正しく実装している（`PermissionService.intersectGrant`、ワイルドカード対応）。しかし `permissionResolver` を渡していないため、**manifest 宣言がそのまま granted になる**:

```ts
const resolvedGrant = await this.permissionResolver?.({...}) ?? options.manifest.permissions
```

つまり user grant の項が常に「全部」になっており、**交差が意味をなさない**。

**Lumi は初回ロード時に必ず同意 UI を出す。** → [extension.md](extension.md)

---

## 10. Phase ごとの実装範囲

| Phase | 内容 |
|---|---|
| **1** | **骨格**。L0 ツールのみ登録するが、Kernel実行契約と Canonicalizer は本番と同じ経路。`decide()` も本番と同じ関数（返す値が L0 ばかりなだけ）。Provenance の型と伝播も入れる |
| **4a** | **本番**。Canonicalizer / BindVerifier の本実装、権限プロンプト UI、Grant、監査ログ閲覧、hash chain、`fs.*`（Class A） |
| **4b** | `ResultVerifier` の実装、`browser.*`（Class B） |
| **4c** | `computer.*`（Class A）。**Invariant 8 の穴（全画面キャプチャ / 座標注入）の決着が前提** |
| **6** | 自律 actor によるツール使用。`escalate_for_self_initiated` が実際に効き始める |

### なぜ Phase 1 から骨格を入れるのか

**後から Permission を入れるのが最も危険。** ツールの呼び出し経路が全部変わる。

L0 しかなくても、`ToolRegistry.invoke` が `canonicalize → decide → bind → verify → execute` を通ることを最初から保証しておけば、Phase 4 は「Canonicalizer と Policy の中身を書く」だけになる。

---

## 11. テスト

**これらは LLM を呼ばずにテストできなければならない。**

| # | テスト |
|---|---|
| 1 | `actor=self_initiated` で L2 が `ask` になる |
| 2 | `actor=self_initiated` で L3 / L4 が `deny` になる |
| 3 | `effective_trust=TAINTED` + 実効 L3 で `ask` に強制昇格する |
| 3b | **`base_risk=L2` + `self_initiated` + `TAINTED` が `ask` になる**（provenance 規則が effective_risk を見ていることの確認） |
| 3c | **`actor=system` が L1 以上で `deny` になる**（Job / idle の制約） |
| 3d | **`decide()` が純粋関数であり、引数が4つだけである**（静的検査。LLM の理由文が届かないこと） |
| 4 | L4 が Grant を持っていても `ask` になる |
| 5 | `non_cancellable` + 副作用あり + L3未満 の Tool が**登録時に例外**になる |
| 5b | **Class B + 副作用あり + L3未満 の Tool が登録時に例外**になる |
| 6 | **Canonicalizer の攻撃ベクタ**: traversal / symlink / UNC / IDN homograph / URL redirect / 短縮URL |
| 7 | 正規化失敗が `deny` になる（fail-closed） |
| 8 | ハードコード不変条件（1〜6）が設定で無効化できない |
| 9 | 権限プロンプトウィンドウへの `input.inject` が拒否される |
| 10 | Grant が scope 外の対象に使えない |
| 11 | Grant の `remaining_uses` が正しく消費される |
| 12 | Grant が Tool から作成・削除できない |
| 13 | 全ての決定が `policy_version` 付きで audit_log に記録される |
| 14 | `audit_log` への `DELETE` / `UPDATE` がコードベースに存在しない（静的検査） |
| 15 | Extension の実効権限が3つの交差になる |
| 16 | 同意 UI を経ずに Extension が granted にならない |
