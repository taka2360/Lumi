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

- **日本語。** 何を変えたかではなく、**なぜ変えたか**が分かる粒度で
- 設計変更を伴う場合、**ADR とドキュメント更新を同じコミットに含める**（コードだけ先に入れない）
- 1コミット1論点。Invariant に関わる変更を他の変更に混ぜない

```
Kernel 実行契約を Class A / Class B に分ける

out-of-process では Handle がプロセスを跨げず BindVerifier が
成立しないため。fs / computer は in-core に移す。

ADR-017
```

## 禁止

- `--no-verify` でフックを飛ばさない。フックが落ちたら**原因を直す**
- 生成物・モデルファイル・音声ライブラリ・Cubism Core をコミットしない
- **非OSS のものをリポジトリに入れない**（Core = MIT の境界を保つ）
- ユーザーの記憶 DB・監査ログ・`CLAUDE.local.md` をコミットしない

## コミット・push のタイミング

**ユーザーが求めたときだけ。** 勝手にコミットや push をしない。
