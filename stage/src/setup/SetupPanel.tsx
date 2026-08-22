/**
 * First-run setup, and the status of the three inference components.
 *
 * Design → docs/architecture/setup.md §2 / §2b, [ADR-034]
 *
 * **Presents fetch / don't-fetch as equal choices** (ADR-019 principle 2).
 * Both buttons are laid out the same size and weight — neither is grayed out.
 * **The default is "not now"** (there's no way to close without pressing
 * either, but "not now" is placed first in order).
 *
 * **Never silently degrades** (principle 4). Both "didn't fetch" and "failed" stay visible,
 * and neither of them lets the character out (ADR-034).
 *
 * ## Three screens, and only ever one of them
 *
 * | when | screen |
 * |---|---|
 * | a question is on screen | **ask**: what would be fetched, and two equal buttons |
 * | `boot` is `blocked` | **incomplete**: everything still missing, how to fix it, and 終了 |
 * | otherwise | a compact status strip |
 *
 * The compact strip is the fail-safe: once all three have to be usable for the character
 * to appear, a running Lumi has nothing to report. **It stays because a drift between
 * Core's phase and the component states should be visible**, not swallowed.
 *
 * The wording lives in `status.ts` as pure functions — **which sentence each state gets
 * is the part worth testing**, and it is not testable from in here.
 */

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { type SetupComponent, useStageStore } from "../core/store";
import { answerSetupPrompt, recheckOllama } from "../core/useCoreConnection";
import { type Locale, translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { useOpenOllamaSite, useQuit } from "../platform/useStageShell";
import { failureText, type StatusLine, statusLines, sttStatus, ttsStatus } from "./status";

/** Short enough to notice an installation finishing, slow enough to stay negligible. */
export const OLLAMA_RECHECK_INTERVAL_MS = 1_000;

function formatGigabytes(bytes: number, locale: Locale): string {
  return `${(bytes / 1_000_000_000).toFixed(1)} ${translate(locale, "setup.model.gb")}`;
}

/** What is being asked about. **The subject of consent is never left implicit.** */
function prompts(
  locale: Locale,
): Record<
  Exclude<SetupComponent, "llm_model">,
  { title: string; body: ReactNode; note: ReactNode }
> {
  return {
    tts: {
      title: translate(locale, "setup.prompt.tts.title"),
      body: (
        <>
          {translate(locale, "setup.prompt.tts.body.before")}
          <b>AivisSpeech Engine</b>
          {translate(locale, "setup.prompt.tts.body.middle")}
          <b>{translate(locale, "setup.prompt.tts.body.strong")}</b>
          {translate(locale, "setup.prompt.tts.body.after")}
        </>
      ),
      note: (
        <>
          {translate(locale, "setup.prompt.tts.note.before")}
          <b>{translate(locale, "setup.prompt.tts.note.strong")}</b>
          {translate(locale, "setup.prompt.tts.note.after")}
        </>
      ),
    },
    stt: {
      title: translate(locale, "setup.prompt.stt.title"),
      body: (
        <>
          {translate(locale, "setup.prompt.stt.body.before")}
          <b>faster-whisper</b>
          {translate(locale, "setup.prompt.stt.body.middle")}
          <b>{translate(locale, "setup.prompt.stt.body.strong")}</b>
          {translate(locale, "setup.prompt.stt.body.after")}
        </>
      ),
      note: translate(locale, "setup.prompt.stt.note"),
    },
  };
}

/** One status line, with its command or download page underneath when there is one. */
function StatusText({ line }: { line: StatusLine }) {
  return (
    <p className={line.tone === "bad" ? "panel__status panel__status--bad" : "panel__status"}>
      {line.text}
      {line.hint && (
        <>
          <br />
          <code className="panel__hint" data-window-gesture="exclude">
            {line.hint}
          </code>
        </>
      )}
    </p>
  );
}

export function SetupPanel() {
  const locale = useLocale();
  const setup = useStageStore((state) => state.setup);
  const prompt = useStageStore((state) => state.prompt);
  const quit = useQuit();
  const [ollamaActionError, setOllamaActionError] = useState(false);
  const [showModelAlternatives, setShowModelAlternatives] = useState(false);
  const ollamaCheckActive = useRef(false);
  const openOllamaSite = useOpenOllamaSite(() => setOllamaActionError(true));
  const ollamaStarting =
    setup.llm.state === "detected" &&
    setup.llm.runtime === "starting" &&
    setup.llm.reason === "ollama_starting";
  const ollamaWaiting =
    setup.llm.state === "not_configured" ||
    (setup.llm.state === "detected" && setup.llm.runtime === "stopped") ||
    ollamaStarting;
  const ollamaChecking =
    setup.llm.state === "detected" && setup.llm.runtime === "starting" && !ollamaStarting;

  const checkOllama = useCallback(async () => {
    if (ollamaCheckActive.current) return;
    ollamaCheckActive.current = true;
    setOllamaActionError(false);
    try {
      await recheckOllama();
    } catch {
      setOllamaActionError(true);
    } finally {
      ollamaCheckActive.current = false;
    }
  }, []);

  useEffect(() => {
    if (prompt || setup.boot !== "blocked" || !ollamaWaiting) return;
    const timer = window.setInterval(() => void checkOllama(), OLLAMA_RECHECK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [checkOllama, ollamaWaiting, prompt, setup.boot]);

  if (prompt) {
    if (prompt.component === "llm_model") {
      const model = prompt.model;
      return (
        <div className="panel panel--model-prompt">
          <h1 className="panel__title">{translate(locale, "setup.prompt.model.title")}</h1>
          {prompt.retry && (
            <p className="panel__status panel__status--bad">{failureText(prompt.reason, locale)}</p>
          )}
          <p className="panel__body">
            {translate(locale, "setup.prompt.model.body.before")}
            <strong>{model?.display_name ?? "Qwen 3.5 9B"}</strong>
            {translate(locale, "setup.prompt.model.body.after")}
          </p>
          {model && (
            <p className="panel__note">
              {translate(locale, "setup.prompt.model.downloadNote", {
                size: formatGigabytes(model.size_bytes, locale),
              })}
            </p>
          )}
          {showModelAlternatives ? (
            <div className="panel__model-options">
              {prompt.alternatives.map((option) => (
                <button
                  type="button"
                  className="panel__button"
                  key={option.model}
                  onClick={() => {
                    setShowModelAlternatives(false);
                    answerSetupPrompt("install", option.model);
                  }}
                >
                  {translate(locale, "setup.prompt.model.downloadNamed", {
                    model: option.display_name,
                    size: formatGigabytes(option.size_bytes, locale),
                  })}
                </button>
              ))}
              <button
                type="button"
                className="panel__button"
                onClick={() => setShowModelAlternatives(false)}
              >
                {translate(locale, "setup.prompt.model.back")}
              </button>
            </div>
          ) : (
            <div className="panel__model-options">
              <button
                type="button"
                className="panel__button"
                onClick={() => answerSetupPrompt("install", model?.model)}
              >
                {translate(locale, "setup.prompt.model.downloadNamed", {
                  model: model?.display_name ?? "Qwen 3.5 9B",
                  size: model ? formatGigabytes(model.size_bytes, locale) : "6.6 GB",
                })}
              </button>
              <button
                type="button"
                className="panel__button"
                onClick={() => setShowModelAlternatives(true)}
              >
                {translate(locale, "setup.prompt.model.choose")}
              </button>
              <button
                type="button"
                className="panel__button"
                onClick={() => answerSetupPrompt("skip")}
              >
                {translate(locale, "setup.skip")}
              </button>
            </div>
          )}
        </div>
      );
    }
    const { title, body, note } = prompts(locale)[prompt.component];
    return (
      <div className="panel">
        <h1 className="panel__title">{title}</h1>
        {prompt.retry && (
          <p className="panel__status panel__status--bad">{failureText(prompt.reason, locale)}</p>
        )}
        <p className="panel__body">{body}</p>
        <p className="panel__note">{note}</p>
        <div className="panel__actions">
          <button type="button" className="panel__button" onClick={() => answerSetupPrompt("skip")}>
            {translate(locale, "setup.skip")}
          </button>
          {/* **The same choice either way**, but after a failure it is worded as a retry:
              "download" would read as though the first attempt had not happened. */}
          <button
            type="button"
            className="panel__button"
            onClick={() => answerSetupPrompt("install")}
          >
            {translate(locale, prompt.retry ? "setup.retry" : "setup.install")}
          </button>
        </div>
      </div>
    );
  }

  const lines = statusLines(setup, locale);

  if (setup.boot === "blocked" && (ollamaWaiting || ollamaChecking)) {
    const otherLines = [ttsStatus(setup.tts, locale), sttStatus(setup.stt, locale)].filter(
      (line): line is StatusLine => line !== null,
    );
    const installedButStopped = setup.llm.state === "detected" && setup.llm.runtime === "stopped";
    return (
      <div className="panel panel--ollama" aria-live="polite">
        <h1 className="panel__title">
          {translate(
            locale,
            ollamaChecking
              ? "setup.ollama.detected.title"
              : ollamaStarting
                ? "setup.ollama.starting.title"
                : installedButStopped
                  ? "setup.ollama.stopped.title"
                  : "setup.ollama.missing.title",
          )}
        </h1>
        {ollamaChecking || ollamaStarting ? (
          <>
            <div className="boot__spinner panel__ollama-spinner" aria-hidden="true" />
            <p className="panel__body">
              {translate(
                locale,
                ollamaStarting ? "setup.ollama.starting.body" : "setup.ollama.checkingModel",
              )}
            </p>
            {ollamaStarting && (
              <p className="panel__note">{translate(locale, "setup.ollama.autoCheck")}</p>
            )}
          </>
        ) : (
          <>
            <p className="panel__body">
              {installedButStopped
                ? translate(locale, "setup.ollama.stopped.body")
                : translate(locale, "setup.ollama.missing.body")}
            </p>
            {!installedButStopped && (
              <p className="panel__note panel__ollama-note">
                <strong>{translate(locale, "setup.ollama.why.strong")}</strong>
                <br />
                {translate(locale, "setup.ollama.why.detail")}
              </p>
            )}
            <p className="panel__note">{translate(locale, "setup.ollama.autoCheck")}</p>
            {ollamaActionError && (
              <p className="panel__status panel__status--bad">
                {translate(locale, "setup.ollama.actionFailed")}
              </p>
            )}
            {!installedButStopped && (
              <div className="panel__actions panel__actions--single">
                <button type="button" className="panel__button" onClick={openOllamaSite}>
                  {translate(locale, "setup.ollama.openSite")}
                </button>
              </div>
            )}
          </>
        )}
        {otherLines.map((line) => (
          <StatusText key={line.text} line={line} />
        ))}
        <div className="panel__actions panel__actions--single panel__quit-action">
          <button type="button" className="panel__button" onClick={quit}>
            {translate(locale, "setup.quit")}
          </button>
        </div>
      </div>
    );
  }

  if (setup.boot === "blocked") {
    // **Not a loading screen.** Nothing is in progress; what happens next is the user's
    // move, so this lists what is missing rather than spinning (ADR-034).
    //
    // The fallback line exists because **a screen that says setup is incomplete without
    // saying what is incomplete is worse than no screen.** It should be unreachable —
    // every blocking state produces a line — so if it ever shows, something drifted.
    const shown: StatusLine[] =
      lines.length > 0
        ? lines
        : [{ tone: "bad", text: translate(locale, "setup.blocked.unknown") }];
    return (
      <div className="panel">
        <h1 className="panel__title">{translate(locale, "setup.blocked.title")}</h1>
        <p className="panel__body">{translate(locale, "setup.blocked.body")}</p>
        {shown.map((line) => (
          <StatusText key={line.text} line={line} />
        ))}
        {/* **Says the work is not lost.** Quitting here is a pause, not a reset */}
        <p className="panel__note panel__note--spaced">
          {translate(locale, "setup.blocked.resume")}
        </p>
        <div className="panel__actions panel__actions--single">
          <button type="button" className="panel__button" onClick={quit}>
            {translate(locale, "setup.quit")}
          </button>
        </div>
      </div>
    );
  }

  if (lines.length === 0) {
    // **Nothing to say.** All three are fine, so no panel is drawn at all
    return null;
  }

  return (
    <div className="panel panel--compact">
      {lines.map((line) => (
        <StatusText key={line.text} line={line} />
      ))}
    </div>
  );
}
