# ADR-032: 読み上げ速度を Core 所有の設定にする

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-19 |
| 関連 | [../architecture/core.md](../architecture/core.md) §6b, [../interfaces/provider.md](../interfaces/provider.md) `TTSProvider`, [../architecture/audio.md](../architecture/audio.md) §6 |
| 実装 | `core/lumi/settings.py`, `core/lumi/agent/reactive.py`, `core/lumi/providers/tts/aivisspeech.py`, `stage/src/settings/Settings.tsx` |

## Decision

読み上げ速度を `lumi.settings` の `tts_speed` として保存する。既定値は `1.2`、有効範囲は `0.5`〜`2.0` 倍とする。
設定ファイルでは文字列として保持する既存の設定形式に合わせ、環境変数 `LUMI_TTS_SPEED` も使用できる。

Stage の設定画面は Core が配信した値をスライダーで表示し、変更は既存の `stage.settings.update` 経路で Core に依頼する。
環境変数で上書きされている場合は既存の設定と同様に編集不可とする。

Core は設定値を各ターンの `VoiceConfig.speed_scale` に反映し、AivisSpeech / VOICEVOX 互換エンジンの `speedScale` として
`audio_query` の結果へ設定する。実行中のターンは開始時に取得した速度を使い、変更は次のターンの合成から反映する。
リップシンクのタイムラインは合成後の実音声長と `speedScale` を使って生成するため、速度変更時も口の動きと音声を同期できる。

## Reason

速度は音量と違い、ユーザーが会話中に調整したい表現上の設定である。一方、Stage が直接音声を加工すると Core が再生状態を正確に把握できず、
音声停止やリップシンクの契約から外れる。Core の既存設定・TTS・Stage 通知の経路に載せることで、権威と表示値を一つに保つ。

## Consequences

- `settings.json` にキーを追加するだけなので、スキーマ版は上げない。既存ファイルで未指定の場合は `1.2` を使う。
- 読み上げ速度の変更は再起動を要求しない。ただし、すでに開始したターンの音声は変更せず、次のターンから反映する。
- 速度の上下限は Core が検証し、Stage の入力制限だけには依存しない。
- 速度を変更しても音量 (`volumeScale`) や Content Pack の話者選択は変えない。
