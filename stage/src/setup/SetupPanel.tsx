/**
 * First-run setup and TTS status display.
 *
 * Design → docs/architecture/setup.md
 *
 * **Presents fetch / don't-fetch as equal choices** (ADR-019 principle 2).
 * Both buttons are laid out the same size and weight — neither is grayed out.
 * **The default is "don't fetch"** (there's no way to close without pressing
 * either, but "don't" is placed first in order).
 *
 * **Never silently degrades** (principle 4). Both "didn't fetch" and "failed" stay visible.
 */

import { type TtsSetupSnapshot, useStageStore } from "../core/store";
import { answerSetupPrompt } from "../core/useCoreConnection";

/** Turns the failure reason (Core's `SetupError.reason`) into display text. **An unrecognized reason is never hidden either.** */
const FAILURE_TEXT: Record<string, string> = {
  origin_not_allowed: "取得元が想定と違いました",
  redirect_not_allowed: "取得中に想定外の配布元へ転送されました",
  redirect_without_location: "取得元の応答が不正でした",
  too_many_redirects: "取得元の転送が多すぎます",
  http_error: "取得元に接続できませんでした",
  size_mismatch: "取得したファイルの大きさが想定と違いました",
  hash_mismatch: "取得したファイルの内容が想定と違いました",
  extract_failed: "展開に失敗しました",
  executable_not_found: "展開結果に実行ファイルがありませんでした",
  tar_not_found: "展開に使う tar が見つかりませんでした",
  network_unreachable: "ネットワークに接続できませんでした",
  cancelled: "中断されました",
  unexpected_error: "想定外のエラーが起きました",
};

function failureText(reason: string | null): string {
  if (!reason) {
    return "取得に失敗しました";
  }
  return FAILURE_TEXT[reason] ?? `取得に失敗しました（${reason}）`;
}

function StatusLine({ tts }: { tts: TtsSetupSnapshot }) {
  // **Checks the process state first.** Painting over "installed but won't start"
  // with "installed" would leave the user with no idea what to fix
  // (docs/architecture/setup.md "Never mix installation state and process state").
  if (tts.runtime === "starting") {
    return (
      <p className="panel__status">
        {tts.engine_name}{" "}
        を起動しています…（初回はエンジンが音声モデルを取得するため数分かかります）
      </p>
    );
  }
  if (tts.runtime === "failed") {
    return (
      <p className="panel__status panel__status--bad">
        {tts.engine_name} を起動できませんでした（入ってはいますが、動いていません）
      </p>
    );
  }

  switch (tts.state) {
    case "installing":
      return (
        <p className="panel__status">
          {tts.engine_name} を取得中… {Math.round((tts.progress ?? 0) * 100)}%
        </p>
      );
    case "failed":
      // **Distinguished from "not installed yet."** Shows what actually happened.
      return <p className="panel__status panel__status--bad">{failureText(tts.reason)}</p>;
    case "not_configured":
      return (
        <p className="panel__status">
          音声合成は未セットアップです（Lumi は動きますが、喋りません）
        </p>
      );
    case "detected":
      return <p className="panel__status">{tts.engine_name} を使います</p>;
    case "installed":
      return (
        <p className="panel__status">
          {tts.engine_name} {tts.version} を使います
        </p>
      );
    default:
      return null;
  }
}

export function SetupPanel() {
  const tts = useStageStore((state) => state.tts);
  const prompt = useStageStore((state) => state.prompt);

  if (prompt) {
    return (
      <div className="panel">
        <h1 className="panel__title">音声合成エンジンを取得しますか？</h1>
        {prompt.retry && (
          <p className="panel__status panel__status--bad">{failureText(prompt.reason)}</p>
        )}
        <p className="panel__body">
          Lumi が声を出すには <b>AivisSpeech Engine</b>（LGPL-3.0）が必要です。 Lumi
          には同梱していないため、<b>公式の配布元から取得します</b>。 取得しない場合も Lumi
          は起動します（喋らないだけです）。
        </p>
        <p className="panel__note">
          これは Lumi が外部へ通信する最初のタイミングです。取得しなければ通信は発生しません。
          エンジン本体は約 200MB です。
          <b>また、エンジンは初回起動時に、エンジン自身が音声モデルを AivisHub から取得します</b>
          （この取得は Lumi の検証の対象外です）。
        </p>
        <div className="panel__actions">
          <button type="button" className="panel__button" onClick={() => answerSetupPrompt("skip")}>
            取得しない
          </button>
          <button
            type="button"
            className="panel__button"
            onClick={() => answerSetupPrompt("install")}
          >
            取得する
          </button>
        </div>
      </div>
    );
  }

  const settled = tts.runtime === "stopped" || tts.runtime === "ready";
  if (
    settled &&
    (tts.state === "unknown" || tts.state === "installed" || tts.state === "detected")
  ) {
    // The panel is never shown in a usable state, or a state where nothing is known yet.
    // But **starting and failed-to-start are shown** (never leaves "why isn't it speaking" unexplained).
    return null;
  }

  return (
    <div className="panel panel--compact">
      <StatusLine tts={tts} />
    </div>
  );
}
