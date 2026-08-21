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

import type { ReactNode } from "react";

import { type SetupComponent, useStageStore } from "../core/store";
import { answerSetupPrompt } from "../core/useCoreConnection";
import { type Locale, translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { useQuit } from "../platform/useStageShell";
import { failureText, type StatusLine, statusLines } from "./status";

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

/** One status line, with its command or download page underneath when there is one. */
function StatusText({ line }: { line: StatusLine }) {
  return (
    <p className={line.tone === "bad" ? "panel__status panel__status--bad" : "panel__status"}>
      {line.text}
      {line.hint && (
        <>
          <br />
          <code className="panel__hint" data-window-drag="exclude">
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
