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
 * | Missing | What is asked of the user |
 * |---|---|
 * | TTS | let Lumi fetch the engine |
 * | LLM | install/start Ollama, then consent before Lumi fetches a model |
 * | STT | let Lumi fetch the model |
 *
 * **None of these is an error**, and none of them is worded as one — but any one of them
 * does stop Lumi from starting (ADR-034), so each line says what is missing rather than
 * what Lumi can still do without it. "Lumi will run, but it will not speak" stopped being
 * true the moment speaking became a precondition.
 *
 * These lines are what the `blocked` screen lists. **Every reason the phase can be
 * `blocked` has to produce one**, or the screen would say setup is incomplete without
 * saying what is incomplete.
 */

import type {
  EmbeddingSetupSnapshot,
  LlmSetupSnapshot,
  SetupSnapshot,
  SttSetupSnapshot,
  TtsSetupSnapshot,
} from "../core/store";
import { type Locale, type MessageKey, translate } from "../i18n";

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
const FAILURE_KEYS: Record<string, MessageKey> = Object.fromEntries(
  [
    "origin_not_allowed",
    "redirect_not_allowed",
    "redirect_without_location",
    "too_many_redirects",
    "http_error",
    "size_mismatch",
    "hash_mismatch",
    "extract_failed",
    "executable_not_found",
    "tar_not_found",
    "network_unreachable",
    "disk_error",
    "model_incomplete",
    "unknown_model",
    "cancelled",
    "unexpected_error",
    "ollama_pull_http_error",
    "ollama_pull_invalid_response",
    "ollama_pull_failed",
    "ollama_pull_incomplete",
    "ollama_pull_unreachable",
    "model_selection_unavailable",
    "settings_save_failed",
  ].map((reason) => [reason, `status.failure.${reason}` as MessageKey]),
);

export function failureText(reason: string | null, locale: Locale = "ja"): string {
  if (!reason) {
    return translate(locale, "status.failure.generic");
  }
  const key = FAILURE_KEYS[reason];
  return key ? translate(locale, key) : translate(locale, "status.failure.unknown", { reason });
}

function percent(progress: number | null): number {
  return Math.round((progress ?? 0) * 100);
}

/** `null` means "nothing worth saying" — this component is fine. */
export function ttsStatus(tts: TtsSetupSnapshot, locale: Locale = "ja"): StatusLine | null {
  const engine = tts.engine_name ?? translate(locale, "setup.engine.generic");
  // **The process state is checked first.** Painting over "installed but won't start" with
  // "installed" would leave the user with nothing to act on
  // (docs/architecture/setup.md "Never mix installation state and process state").
  //
  // **`starting` deliberately produces no line.** These lines are the list of what the user
  // still has to resolve, and an engine that is warming up in the background is not on it —
  // it needs nothing from them, and it goes away on its own. The one screen that can render
  // this while the engine is coming up is the incomplete-setup screen, and putting "starting
  // AivisSpeech…" under "Lumi can start once the following are in place" would read as a
  // fourth thing to wait for. When it comes up broken, `failed` below says so.
  if (tts.runtime === "failed") {
    return {
      tone: "bad",
      text: translate(locale, "status.tts.failed", { engine }),
    };
  }

  switch (tts.state) {
    case "installing":
      return {
        tone: "normal",
        text: translate(locale, "status.tts.installing", {
          engine,
          percent: percent(tts.progress),
        }),
      };
    case "failed":
      // **Distinguished from "not fetched yet."** Says what actually happened
      return { tone: "bad", text: failureText(tts.reason, locale) };
    case "not_configured":
      return {
        tone: "normal",
        text: translate(locale, "status.tts.missing"),
      };
    default:
      return null;
  }
}

/**
 * Lumi never installs or starts Ollama. It can fetch an allowlisted model only after an
 * explicit setup choice (ADR-037).
 */
export function llmStatus(llm: LlmSetupSnapshot, locale: Locale = "ja"): StatusLine | null {
  switch (llm.state) {
    case "not_configured":
      return {
        tone: "normal",
        text: translate(locale, "status.llm.missing"),
        hint: translate(locale, "status.llm.installHint"),
      };
    case "model_missing":
      return {
        tone: "normal",
        text: translate(locale, "status.llm.modelMissing", { model: llm.model ?? "" }).trim(),
      };
    case "model_installing":
      return null;
    case "model_failed":
      return { tone: "bad", text: failureText(llm.reason, locale) };
    case "detected":
      // Installed and found, but the process still has to answer for Lumi to reply.
      // **`failed` is not `stopped`**: one is started by the user, the other is looked into
      if (llm.runtime === "stopped") {
        return { tone: "normal", text: translate(locale, "status.llm.stopped") };
      }
      if (llm.runtime === "failed") {
        return { tone: "bad", text: translate(locale, "status.llm.failed") };
      }
      return null;
    default:
      return null;
  }
}

export function sttStatus(stt: SttSetupSnapshot, locale: Locale = "ja"): StatusLine | null {
  if (stt.runtime === "failed") {
    return { tone: "bad", text: translate(locale, "status.stt.failed") };
  }
  switch (stt.state) {
    case "installing":
      return {
        tone: "normal",
        text: translate(locale, "status.stt.installing", { percent: percent(stt.progress) }),
      };
    case "failed":
      return { tone: "bad", text: failureText(stt.reason, locale) };
    case "not_configured":
      return {
        tone: "normal",
        text: translate(locale, "status.stt.missing"),
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
 *
 * **All of them, not the first one.** Fixing what one line asks for and then being handed
 * the next is how a two-minute setup turns into three restarts.
 */
/**
 * The embedding model (ADR-041).
 *
 * **Never a `bad` tone, even when the fetch failed.** Every other line here is a reason
 * Lumi cannot start; this one is a reason memory search is worse than it could be, and
 * colouring the two the same would make the list unreadable as a list of blockers.
 */
export function embeddingStatus(
  embedding: EmbeddingSetupSnapshot,
  locale: Locale = "ja",
): StatusLine | null {
  switch (embedding.state) {
    case "installing":
      return {
        tone: "normal",
        text: translate(locale, "boot.embedding.fetching", {
          percent: percent(embedding.progress),
        }),
      };
    case "failed":
      return { tone: "normal", text: translate(locale, "status.embedding.failed") };
    case "not_configured":
      return { tone: "normal", text: translate(locale, "status.embedding.missing") };
    default:
      return null;
  }
}

export function statusLines(setup: SetupSnapshot, locale: Locale = "ja"): StatusLine[] {
  return [
    ttsStatus(setup.tts, locale),
    llmStatus(setup.llm, locale),
    sttStatus(setup.stt, locale),
    // **Last.** It is the only line that does not stop Lumi running, and putting it above
    // one that does would misrepresent which of them the user has to act on.
    embeddingStatus(setup.embedding, locale),
  ].filter((line): line is StatusLine => line !== null);
}
