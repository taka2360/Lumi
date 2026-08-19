/**
 * First-run setup, and the status of the three inference components.
 *
 * Design → docs/architecture/setup.md §2 / §2b
 *
 * **Presents fetch / don't-fetch as equal choices** (ADR-019 principle 2).
 * Both buttons are laid out the same size and weight — neither is grayed out.
 * **The default is "don't fetch"** (there's no way to close without pressing
 * either, but "don't" is placed first in order).
 *
 * **Never silently degrades** (principle 4). Both "didn't fetch" and "failed" stay visible.
 *
 * The wording lives in `status.ts` as pure functions — **which sentence each state gets
 * is the part worth testing**, and it is not testable from in here.
 */

import type { ReactNode } from "react";

import { type SetupComponent, useStageStore } from "../core/store";
import { answerSetupPrompt } from "../core/useCoreConnection";
import { browserLocale, type Locale, translate } from "../i18n";
import { failureText, statusLines } from "./status";

/** What is being asked about. **The subject of consent is never left implicit.** */
function prompts(
  locale: Locale,
): Record<SetupComponent, { title: string; body: ReactNode; note: ReactNode }> {
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

export function SetupPanel() {
  const locale = browserLocale();
  const setup = useStageStore((state) => state.setup);
  const prompt = useStageStore((state) => state.prompt);

  if (prompt) {
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
          <button
            type="button"
            className="panel__button"
            onClick={() => answerSetupPrompt("install")}
          >
            {translate(locale, "setup.install")}
          </button>
        </div>
      </div>
    );
  }

  const lines = statusLines(setup, locale);
  if (lines.length === 0) {
    // **Nothing to say.** All three are fine, so no panel is drawn at all
    return null;
  }

  return (
    <div className="panel panel--compact">
      {lines.map((line) => (
        <p
          key={line.text}
          className={line.tone === "bad" ? "panel__status panel__status--bad" : "panel__status"}
        >
          {line.text}
          {line.hint && (
            <>
              <br />
              <code className="panel__hint">{line.hint}</code>
            </>
          )}
        </p>
      ))}
    </div>
  );
}
