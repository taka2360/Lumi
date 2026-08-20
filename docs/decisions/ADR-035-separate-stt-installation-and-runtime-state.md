# ADR-035: STT の導入状態とロード状態を分離する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-20 |
| 関連 | [../architecture/setup.md](../architecture/setup.md) §2b, [ADR-023](ADR-023-llm-runtime-and-model-acquisition.md), [ADR-034](ADR-034-gate-startup-on-complete-setup.md) |
| 実装 | `core/lumi/setup/state.py`, `core/lumi/setup/coordinator.py`, `core/lumi/agent/runtime.py`, `stage/src/core/store.ts`, `stage/src/setup/status.ts` |

---

## Decision

**STT も、モデルの導入状態と Provider のロード状態を別の軸として持つ。**

1. `SttSetup.state` はモデル取得の状態だけを表す。`failed` は、取得・検証・展開・書き込みに
   失敗した状態であり、モデルのロード失敗には使わない
2. `SttSetup.runtime` に既存の `engine_runtime`（`stopped` / `starting` / `ready` / `failed`）を
   共用する。ここでの runtime は OS プロセスではなく、**Provider が推論可能かという実行状態**を表す
3. STT が「使える」のは `state == installed` **かつ** `runtime == ready` のときだけとする
4. `warm_stt()` は、導入済みモデルに対して `starting` を報告してから Provider をロードし、
   成功を `ready`、`ProviderError` を `failed` として報告する
5. Stage は `state == failed` を取得失敗として表示し、`runtime == failed` を
   **取得済みだがロードできない状態**として別の文言で表示する

## Reason

モデルのファイルがすべて揃っていることと、CTranslate2 がそのモデルをロードできることは別である。
後者は CUDA / cuDNN、compute type、VRAM、壊れた重みなどの理由で失敗しうる。

この失敗を `SttSetupState.FAILED` に入れると、Stage はダウンロード、SHA-256、ディスク書き込みなどの
`SetupError` として表示する。**「取得済みだが動かない」ユーザーに再取得の失敗を案内することになり、
導入状態と実行状態を分離した TTS / LLM の規則に反する。**

STT は外部プロセスではないが、起動時に重みをメモリへロードする明確なライフサイクルを持つ。
したがって runtime 軸の意味は「プロセスがあるか」ではなく、3 Provider に共通する
**いま推論できるか**である。

## Alternatives

### 1. ロード失敗を `SttSetupState.FAILED` にする

`boot` を `blocked` にするだけなら最小だが、取得失敗とロード失敗を同じ状態と文言に潰す。
ユーザーに求める解決方法が違うため採らない。

### 2. ロード失敗はログだけに残し、`installed` を維持する

導入状態は正しいが、`installed` だけで `ready` になる従来の導出ではキャラクターと音声入力が
開始される。ADR-034 の「3つとも使えるときだけ起動」を破るため採らない。

### 3. STT 専用の runtime enum を作る

値と遷移は既存の `engine_runtime` と同じであり、別 enum は wire 上の語彙を重複させる。
`EngineRuntime` を Provider の実行状態として共用する。

## Consequences

- `stage.setup.state.stt` に `runtime` が追加される。値は既存の `engine_runtime` を使う
- STT のロード中は、ほかに確定した不足がなければ `boot: starting` になる
- STT のロード失敗後も `state: installed` は維持され、`runtime: failed` により `boot: blocked` になる
- 取得の再試行 UI とロード失敗の案内を混同しない
