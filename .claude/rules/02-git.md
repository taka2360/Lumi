# Git

## ブランチ

**main で直接作業しない。**

| 種別 | 命名 |
|---|---|
| 機能追加 | `feat/p<phase>-<topic>` 例: `feat/p0-tauri-transparent-window` |
| 修正 | `fix/<topic>` |
| ドキュメント・ADR | `docs/<topic>` |
| 実験・スパイク | `spike/<topic>`（**マージしない前提。捨てる**） |

## コミット

- **英語。** 何を変えたかではなく、**なぜ変えたか**が分かる粒度で
- 設計変更を伴う場合、**ADR とドキュメント更新を同じコミットに含める**（コードだけ先に入れない）
- 1コミット1論点。Invariant に関わる変更を他の変更に混ぜない

```text
Split the Kernel execution contract into Class A and Class B

Handles cannot cross process boundaries in out-of-process execution,
so BindVerifier cannot work. Move fs and computer in-core.

ADR-017
```

## 禁止

- `--no-verify` でフックを飛ばさない。フックが落ちたら**原因を直す**
- 生成物・モデルファイル・音声ライブラリ・Cubism Core をコミットしない
- **非OSS のものをリポジトリに入れない**（Core = MIT の境界を保つ）
- ユーザーの記憶 DB・監査ログ・`CLAUDE.local.md` をコミットしない

## コミット・push のタイミング

**ユーザーが求めたときだけ。** 勝手にコミットや push をしない。
