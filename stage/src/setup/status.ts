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
    "model_incomplete",
    "unknown_model",
    "cancelled",
    "unexpected_error",
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
  if (tts.runtime === "starting") {
    return {
      tone: "normal",
      text: translate(locale, "status.tts.starting", { engine }),
    };
  }
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
 * **The one component whose message asks the user to act themselves** — Lumi neither
 * fetches nor starts Ollama (ADR-023).
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
      // **Installed, running, just missing the model.** A different instruction entirely
      return {
        tone: "normal",
        text: translate(locale, "status.llm.modelMissing", { model: llm.model ?? "" }).trim(),
        hint: `ollama pull ${llm.model ?? ""}`.trim(),
      };
    case "detected":
      return llm.runtime === "stopped"
        ? {
            tone: "normal",
            text: translate(locale, "status.llm.stopped"),
          }
        : null;
    default:
      return null;
  }
}

export function sttStatus(stt: SttSetupSnapshot, locale: Locale = "ja"): StatusLine | null {
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
 */
export function statusLines(setup: SetupSnapshot, locale: Locale = "ja"): StatusLine[] {
  return [
    ttsStatus(setup.tts, locale),
    llmStatus(setup.llm, locale),
    sttStatus(setup.stt, locale),
  ].filter((line): line is StatusLine => line !== null);
}
