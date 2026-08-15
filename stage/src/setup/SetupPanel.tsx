/**
 * 初回セットアップと TTS の状態表示。
 *
 * 設計 → docs/architecture/setup.md
 *
 * **取得する / しない を同等に提示する**（ADR-019 の原則2）。
 * どちらのボタンも同じ大きさ・同じ強さで並べ、片方を灰色にしない。
 * **既定は「取得しない」**（何も押さずに閉じることはできないが、順序は「しない」を先に置く）。
 *
 * **黙って劣化しない**（原則4）。取得しなかった / 失敗したは、どちらも表示に残る。
 */

import { type TtsSetupSnapshot, useStageStore } from "../core/store";
import { answerSetupPrompt } from "../core/useCoreConnection";

/** 失敗理由（Core の `SetupError.reason`）を日本語にする。**知らない理由も隠さない。** */
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
  switch (tts.state) {
    case "installing":
      return (
        <p className="panel__status">
          {tts.engine_name} を取得中… {Math.round((tts.progress ?? 0) * 100)}%
        </p>
      );
    case "failed":
      // **「まだ入れていない」と区別する。** 何が起きたかを出す。
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

  if (tts.state === "unknown" || tts.state === "installed" || tts.state === "detected") {
    // 使える状態と、まだ何も分かっていない状態では、パネルを出さない。
    return null;
  }

  return (
    <div className="panel panel--compact">
      <StatusLine tts={tts} />
    </div>
  );
}
