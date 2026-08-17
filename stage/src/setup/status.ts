/**
 * What the setup panel says, as **pure functions**.
 *
 * Design → docs/architecture/setup.md §2 / §2b
 *
 * Kept out of the component so the wording can be tested directly. The wording is the
 * whole point of these states: **"not installed" and "installed but not running" are
 * different sentences**, and getting that wrong sends the user to fix something that
 * isn't broken.
 *
 * ## What each component's absence means
 *
 * | Missing | Lumi still | What is asked of the user |
 * |---|---|---|
 * | TTS | listens and understands | let Lumi fetch the engine |
 * | LLM | listens, and says nothing | install Ollama, or pull the model **themselves** |
 * | STT | speaks when spoken to in text | let Lumi fetch the model |
 *
 * **None of these is an error**, and none of them is worded as one.
 */

import type {
  LlmSetupSnapshot,
  SetupSnapshot,
  SttSetupSnapshot,
  TtsSetupSnapshot,
} from "../core/store";

export interface StatusLine {
  /** `bad` is for something that actually went wrong — **never for "not set up yet."** */
  tone: "normal" | "bad";
  text: string;
  /** A command to run, or where to get something. Shown in monospace. */
  hint?: string;
}

/**
 * Turns Core's `SetupError.reason` into display text.
 * **An unrecognized reason is still shown**, never swallowed.
 */
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
  model_incomplete: "取得したモデルのファイルが揃いませんでした",
  unknown_model: "指定されたモデルは取得対象ではありません",
  cancelled: "中断されました",
  unexpected_error: "想定外のエラーが起きました",
};

export function failureText(reason: string | null): string {
  if (!reason) {
    return "取得に失敗しました";
  }
  return FAILURE_TEXT[reason] ?? `取得に失敗しました（${reason}）`;
}

function percent(progress: number | null): number {
  return Math.round((progress ?? 0) * 100);
}

/** `null` means "nothing worth saying" — this component is fine. */
export function ttsStatus(tts: TtsSetupSnapshot): StatusLine | null {
  const engine = tts.engine_name ?? "音声合成エンジン";
  // **The process state is checked first.** Painting over "installed but won't start" with
  // "installed" would leave the user with nothing to act on
  // (docs/architecture/setup.md "Never mix installation state and process state").
  if (tts.runtime === "starting") {
    return {
      tone: "normal",
      text: `${engine} を起動しています…（初回はエンジンが音声モデルを取得するため数分かかります）`,
    };
  }
  if (tts.runtime === "failed") {
    return {
      tone: "bad",
      text: `${engine} を起動できませんでした（入ってはいますが、動いていません）`,
    };
  }

  switch (tts.state) {
    case "installing":
      return { tone: "normal", text: `${engine} を取得中… ${percent(tts.progress)}%` };
    case "failed":
      // **Distinguished from "not fetched yet."** Says what actually happened
      return { tone: "bad", text: failureText(tts.reason) };
    case "not_configured":
      return {
        tone: "normal",
        text: "音声合成は未セットアップです（Lumi は動きますが、喋りません）",
      };
    default:
      return null;
  }
}

/**
 * **The one component whose message asks the user to act themselves** — Lumi neither
 * fetches nor starts Ollama (ADR-023).
 */
export function llmStatus(llm: LlmSetupSnapshot): StatusLine | null {
  switch (llm.state) {
    case "not_configured":
      return {
        tone: "normal",
        text: "Ollama が見つかりません（Lumi は聞こえていますが、返事ができません）",
        hint: "ollama.com からインストールしてください",
      };
    case "model_missing":
      // **Installed, running, just missing the model.** A different instruction entirely
      return {
        tone: "normal",
        text: `モデル ${llm.model ?? ""} がまだありません`.trim(),
        hint: `ollama pull ${llm.model ?? ""}`.trim(),
      };
    case "detected":
      return llm.runtime === "stopped"
        ? {
            tone: "normal",
            text: "Ollama が起動していません（入ってはいます）。起動すると返事ができるようになります",
          }
        : null;
    default:
      return null;
  }
}

export function sttStatus(stt: SttSetupSnapshot): StatusLine | null {
  switch (stt.state) {
    case "installing":
      return { tone: "normal", text: `音声認識モデルを取得中… ${percent(stt.progress)}%` };
    case "failed":
      return { tone: "bad", text: failureText(stt.reason) };
    case "not_configured":
      return {
        tone: "normal",
        text: "音声認識は未セットアップです（Lumi は喋りますが、聞き取れません）",
      };
    default:
      return null;
  }
}

/**
 * Every line worth showing, in a fixed order. **Empty means everything is fine**, and the
 * panel is then not drawn at all.
 *
 * Order is TTS → LLM → STT, matching the order the pipeline fails in from the user's
 * point of view: not speaking is noticed first, then not answering, then not hearing.
 */
export function statusLines(setup: SetupSnapshot): StatusLine[] {
  return [ttsStatus(setup.tts), llmStatus(setup.llm), sttStatus(setup.stt)].filter(
    (line): line is StatusLine => line !== null,
  );
}
