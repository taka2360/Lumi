---
paths:
  - "core/tests/**/*.py"
  - "**/*_test.py"
  - "**/test_*.py"
  - "**/*.test.ts"
  - "**/*.test.tsx"
  - "shell/**/tests/**/*.rs"
---

# テスト

## 大原則

> **LLM を呼ばずにテストできなければならない。呼ばないとテストできないなら、設計が間違っている。**

LLM が関わる部分は「**プロンプト構築のスナップショットテスト**」に落とす（入力の正しさを検証し、出力は検証しない）。

## 決定論的であること

- Drive の計算、Policy の `decide()`、salience の減衰、検索の切り落とし、projection、ウィンドウ設定、ヒットテスト
  → **すべて純粋関数として書き、純粋関数としてテストする**
- 時刻は引数で渡す（`now: datetime`）。テスト内で `datetime.now()` を呼ばせない

## 各ドキュメントのテスト表を実装する

`docs/` の各ファイル末尾に「テスト」表がある。**これが受け入れ条件。** 実装時にそこから起こす。

特に落としてはいけないもの:

| 領域 | テスト |
|---|---|
| Kernel | `running` な Activity が同時に2つ存在しない（preempt の途中を含む） |
| Kernel | idle が、他が foreground の間 `suspended` になる |
| Kernel | foreground が推論を要求すると Job の `inference_lease` が revoke される |
| Tool | **`BindVerifier` が scope と handle の不一致を検出する**（意図的に嘘の handle を返すモック Tool で検証） |
| Tool | 正規化の攻撃ベクタ: traversal / symlink / UNC / IDN homograph / URL redirect / 短縮 URL |
| Tool | verify 失敗時に `execute` が呼ばれない |
| Policy | `base_risk=L2` + `self_initiated` + `TAINTED` が `ask` になる |
| Provenance | **`user_confirmed` 以外の経路で `trust_level = TRUSTED` にならない** |
| Provenance | **雑談ターンが tainted にならない / `session_trust` が sticky** |
| Audio | **speech-start から再生停止までのレイテンシ**（mute latency < 50ms） |
| Audio | **負荷時（LLM 推論 + TTS 生成の同時実行）にバッファアンダーランが発生しない** |
| Audio | **大きい音では再生中でも必ず割り込める**（抑制していないことの確認） |
| Autonomy | **24時間分の World State 系列を流し、割り込み回数が予算内に収まる** |

## 静的検査もテストである

[contracts/authority-matrix.md](../../docs/contracts/authority-matrix.md) の16項目を CI で回す。
grep で足りるものは grep でよい（例: `trust_level = TRUSTED` の書き込み箇所の列挙）。

## fail-closed のテストを忘れない

**「正しく動く」より「壊れたときに止まる」を先にテストする。**
メタデータ欠落 Tool の登録 / 未登録 lane / Class A lane を宣言した out-of-process manifest / 正規化失敗
→ **すべて起動時または実行前に例外・deny になること。**
