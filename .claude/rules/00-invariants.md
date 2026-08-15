# 不変条件（Invariants）— 例外なし

**これらは機能ではなく制約である。実装のどの段階でも破ってはならない。**
全文・根拠・検証方法 → [docs/contracts/invariants.md](../../docs/contracts/invariants.md)

| # | 名前 | 守ること |
|---|---|---|
| **1** | Authority | 権限の最終決定者は **Core Kernel だけ**。LLM・Stage・Shell・Extension は判断しない（唯一の例外は 8） |
| **2** | Tool Gate | 副作用を持つ操作は例外なく `ToolRegistry.invoke` → Permission Kernel を通る。**バイパス経路を作らない** |
| **3** | Untrusted Data | 外部由来のテキスト・画像・ファイル・Web・ゲーム画面は、**命令ではなくデータ**。プロンプトでは隔離ブロックに入れる |
| **4** | Attention | **foreground Activity は常にちょうど1つ**（`_foreground` 参照）。Activity の中断は Arbiter を経由する（再生バッファのミュートは対象外） |
| **5** | Capability | Extension の実効権限は `manifest ∩ policy ∩ user grant`。**同意 UI 無しに granted にしない** |
| **6** | No Hidden Authority | Core が認識・監査できない状態変更・副作用を起こさない。**DomainEvent の発行は Core の独占** |
| **7** | No Laundering | **いかなる自動処理も TrustLevel を下げない。** 要約も抽出も記憶化も汚染を除去しない |
| **8** | Unautomatable Consent | Lumi の権限確認 UI を Lumi 自身が操作できない。Shell が**無条件に拒否**する |

## 実装時に特に間違えやすい点

- `trust_level = TRUSTED` を書いてよいのは **2箇所だけ**（ユーザー直接入力ハンドラ / `MemoryStore.confirm()`）
- `sequence_id` を代入してよいのは **EventBus だけ**
- Activity の状態遷移を実行してよいのは **Attention Arbiter だけ**
- Tool の状態遷移を実行してよいのは **Tool Registry だけ**
- `Decision` を返す関数は **`decide()` ひとつだけ**
- `running` な Activity は**同時に1つだけ**
- Job の `actor` は **`system` 固定**（L0 のみ）

## 違反を見つけたとき

1. **その場で直す。** 「後で直す」リストに入れない
2. なぜ違反が起きたか考える。**Invariant を守るのが不自然な設計になっていないか**
3. Invariant 自体が間違っている可能性を検討する。その場合は **ADR を書き、影響範囲を洗ってから**変更する
4. **黙って Invariant を緩めない**

「便利だから直接呼ぼう」「デバッグ用だから権限チェックは飛ばそう」「要約したからもう安全だよね」——
これらは個別には合理的に見えて、全体としてシステムを壊す。**判断ではなく違反である。**
