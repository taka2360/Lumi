/**
 * The question about one component — TTS, STT, or the embedding model.
 *
 * **Presents fetch / don't-fetch as equal choices** (ADR-019 principle 2): same size, same
 * weight, neither greyed out, and **"not now" comes first**.
 *
 * The copy names what would be fetched, from where, and how big it is. **The subject of
 * consent is never left implicit** — that is why each component has its own wording rather
 * than one sentence with a noun substituted in.
 */

import type { ReactNode } from "react";

import type { SetupComponent, SetupPrompt } from "../../core/store";
import { answerSetupPrompt } from "../../core/useCoreConnection";
import { type Locale, translate } from "../../i18n";
import { failureText } from "../status";

type Askable = Exclude<SetupComponent, "llm_model" | "all">;

/** What is being asked about. **The subject of consent is never left implicit.** */
function prompts(
  locale: Locale,
): Record<Askable, { title: string; body: ReactNode; note: ReactNode }> {
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
    embedding: {
      title: translate(locale, "setup.prompt.embedding.title"),
      body: (
        <>
          {translate(locale, "setup.prompt.embedding.body.before")}
          <b>Harrier-OSS-v1 270M</b>
          {translate(locale, "setup.prompt.embedding.body.after")}
        </>
      ),
      // **The one component whose "not now" costs nothing that stops Lumi working.**
      // Saying so is the difference between an informed decline and a worried one.
      note: translate(locale, "setup.prompt.embedding.note"),
    },
  };
}

export function ComponentPrompt({
  prompt,
  component,
  locale,
}: {
  prompt: SetupPrompt;
  component: Askable;
  locale: Locale;
}) {
  const { title, body, note } = prompts(locale)[component];
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
