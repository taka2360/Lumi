---
paths:
  - "extensions/**"
  - "core/lumi/extensions/**/*.py"
  - "content/**"
---

# Extension / Content Pack

設計 → [architecture/extension.md](../../docs/architecture/extension.md), [interfaces/extension.md](../../docs/interfaces/extension.md), [ADR-005](../../docs/decisions/ADR-005-extension-two-mechanisms.md), [ADR-017](../../docs/decisions/ADR-017-out-of-process-tool-contract.md)

## 2機構（+ Renderer）

| | Provider | Capability |
|---|---|---|
| `runtime` | `in-core` | `out-of-process` |
| 例 | LLM / STT / TTS / Embedding / Vision | Browser / GameAgent / Sensor / Widget |
| 信頼 | **`official` 必須** | **untrusted。capability-gated** |
| Tool | — | **Class B の lane のみ**（`browser` / `game` / `widget`） |

**`untrusted + in-core` は manifest 検証でロードを拒否する。** この組み合わせがあると Extension の安全設計全体が無意味になる。

## Extension にしないもの

**`fs.*` / `computer.*` は Extension にしない。in-core built-in Tool。**

Handle はプロセスを跨げないため、out-of-process では `BindVerifier` が成立せず、
TOCTOU 防御が「Extension を信頼する」に退化する。これらは第三者コードではないので隔離の動機も無い。

## consent を飛ばさない

```
discover → validate → consent ★ → load → announce → ready
```

- **consent 無しに ready にしない。** AIRI は `permissionResolver` 未指定で manifest がそのまま granted になっており、交差モデルが意味をなしていない
- 同意結果は永続化し、**manifest が変わったら再同意**を求める
- **「全部許可」という選択肢を UI に置かない**
- 実効権限 = `manifest ceiling ∩ policy ∩ user grant`

## プロトコル（`ext.*`）

- Extension が送れるのは **Signal と Response だけ**。**DomainEvent を送る経路を作らない**
- `ext.tool.invoke` に渡すのは**正規化済み `SecurityScope` のみ。Handle（fd / HWND / PID）は渡さない**
- `ext.tool.result` は **`acted_on`（実際に操作した対象）を必須**にする。Kernel の `ResultVerifier` が検証し、scope 外なら結果を破棄して `denied` として記録する
- Extension の出力には **Core が `provenance = untrusted` を付与する**（Extension の自己申告を信じない）

## 障害時

**Extension の障害で Lumi 本体が止まってはならない。**
プロセス落ち → 該当 capability を無効化して Lumi は動き続ける。
応答なし → タイムアウトして該当 tool 呼び出しのみ失敗させる。

## Sensor

- Sensor は **World facet を直接書かない。** Signal を送り、**Core が書く**
- manifest で宣言した key 以外は Core が拒否する
- **ウィンドウタイトルを取らない**（機密情報が入りうる）

## Content Pack — コードを含めない

```
content/characters/lumi/
├── character.toml / model.vrm / voice.toml / expressions.toml / motions/
```

Content Pack は共有・配布されやすい。**コードを含むと Extension と同じ脅威になる。**
読み込み時に `.py` / `.js` / 実行可能ファイルが含まれていたら**拒否する**。

キャラクターの振る舞いを変えたいなら人格プロンプトと表情マッピングで表現する。足りないなら Extension にする。
