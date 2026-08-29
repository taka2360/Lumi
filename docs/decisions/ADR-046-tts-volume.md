# ADR-046: 音量を Core 所有の設定にし、Content Pack の音量に対する倍率で表す

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-29 |
| 関連 | [ADR-032](ADR-032-tts-speed.md), [../architecture/core.md](../architecture/core.md) §6b, [../interfaces/provider.md](../interfaces/provider.md) `TTSProvider`, [../architecture/extension.md](../architecture/extension.md) §9 |
| 実装 | `core/lumi/settings.py`, `core/lumi/agent/reactive.py`, `core/lumi/agent/runtime.py`, `stage/src/settings/Settings.tsx` |

## Decision

音量を `lumi.settings` の `tts_volume` として保存する。**値は Content Pack の `voice.volume` に対する倍率**であり、
既定値は `1.0`、有効範囲は `0.0`〜`2.0` 倍とする。環境変数 `LUMI_TTS_VOLUME` でも指定できる。

Core は各ターンの `VoiceConfig.volume_scale` を `pack.voice.volume × tts_volume` として組み立て、
エンジンが受け付ける上限 `2.0` を超える場合は Core が clamp して**その事実をログに残す**。
Stage の設定画面は倍率をスライダーで表示し、**百分率（既定 = 100%）として見せる**。
変更は既存の `stage.settings.update` 経路で Core に依頼し、環境変数で上書きされている場合は編集不可とする。
実行中のターンは開始時に取得した倍率を使い、変更は次のターンの合成から反映する。

## Reason

**「今の音量」は Content Pack が決めている。** `voice.toml` の `volume` はキャラクターの声の大きさとして
作者が書いた値であり、設定 UI がその値を直接書き換えると、**キャラクターを入れ替えたときにユーザーの好みが消える**か、
逆に**作者の意図した声量がユーザー設定に踏み潰される**かのどちらかになる。倍率にすれば両方が独立して残る。

倍率にすることで、**既定値 `1.0` が「今ちょうど鳴っている音量」と厳密に一致する。** 絶対値を既定にすると、
`voice.volume` を変えた Content Pack で「100%」が別の音量を指し、表示値が嘘になる。

音量を Stage 側で加工しない理由は ADR-032 と同じである。Stage が音声を加工すると Core が再生状態を正確に把握できず、
音声停止・リップシンク・AEC の参照信号（`SpeakerPlayback.reference` は「実際にスピーカーへ届いた波形」でなければならない）の
契約から外れる。既存の Core 設定 → TTS → Stage 通知の経路に載せることで、権威と表示値を一つに保つ。

## Alternatives

| 選択肢 | 利点 | 採らなかった理由 |
|---|---|---|
| `volume_scale` の**絶対値**を設定にする | エンジンのパラメータと1対1で分かりやすい | 「今の音量 = 100%」が成立しない。Content Pack を替えると既定値の意味が変わる |
| 再生側（`SpeakerPlayback`）でゲインを掛ける | 再生中でも即座に効く | コールバックで掛けると**参照リングが実際の出力と食い違い**、Phase 2 の AEC が壊れる。`write()` で掛けても先読み合成済みの分には効かず、「即座」にはならない |
| OS のアプリ音量に委ねる | 実装ゼロ | Lumi 自身が状態として持てず、設定 UI に出せない。**Core が認識できない状態変更**になる（Invariant 6 の趣旨） |

## Trade-offs

**受け入れるコスト。** 音量の変更は**次のターンから効く。** すでに合成・先読みされた音声は変わらない。
また倍率の上限が `2.0` なので、`voice.volume` が小さい Content Pack では大きくできる幅も小さい。
**それは作者が決めた声量の範囲であり、そこを超えたいときは Content Pack を直すのが正しい経路である。**

**得るもの。** ユーザーの音量の好みと、Content Pack が持つキャラクターの声量が、互いを壊さずに共存する。

## Consequences

- `settings.json` にキーを追加するだけなので、スキーマ版は上げない。既存ファイルで未指定の場合は `1.0` を使う。
- 音量の変更は再起動を要求しない。`locale` / `tts_speed` と同じ「即時反映」の側に入る。
- 上下限は Core が検証し、Stage の入力制限だけには依存しない。
- 音量を変えても読み上げ速度・Content Pack の話者選択・クレジットは変わらない。
- `voice.toml` の `volume` の意味は変わらない。**倍率の基準になる**という位置づけが加わるだけである。
