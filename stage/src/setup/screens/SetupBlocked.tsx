/**
 * Setup is unfinished, so there is no character to show (ADR-034).
 *
 * **Not a loading screen.** Nothing is in progress; what happens next is the user's move,
 * so this lists what is missing rather than spinning.
 */

import { type Locale, translate } from "../../i18n";
import { StatusText } from "../StatusText";
import type { StatusLine } from "../status";

export function SetupBlocked({
  lines,
  locale,
  onQuit,
}: {
  lines: StatusLine[];
  locale: Locale;
  onQuit: () => void;
}) {
  // **A screen that says setup is incomplete without saying what is incomplete is worse
  // than no screen.** This should be unreachable — every blocking state produces a line —
  // so if it ever shows, something drifted.
  const shown: StatusLine[] =
    lines.length > 0 ? lines : [{ tone: "bad", text: translate(locale, "setup.blocked.unknown") }];

  return (
    <div className="panel">
      <h1 className="panel__title">{translate(locale, "setup.blocked.title")}</h1>
      <p className="panel__body">{translate(locale, "setup.blocked.body")}</p>
      {shown.map((line) => (
        <StatusText key={line.text} line={line} />
      ))}
      {/* **Says the work is not lost.** Quitting here is a pause, not a reset */}
      <p className="panel__note panel__note--spaced">{translate(locale, "setup.blocked.resume")}</p>
      <div className="panel__actions panel__actions--single">
        <button type="button" className="panel__button" onClick={onQuit}>
          {translate(locale, "setup.quit")}
        </button>
      </div>
    </div>
  );
}
