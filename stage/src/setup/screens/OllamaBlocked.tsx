/**
 * Startup is blocked on Ollama.
 *
 * **Lumi never fetches Ollama** (ADR-023), so this screen is the only one that waits on
 * something the user has to do — and it says which of the four situations it is, because
 * telling someone to install what they already have is worse than saying nothing.
 *
 * TTS and STT lines come along too: **all of what is missing, not the first thing**
 * (ADR-034). Fixing one item and then being handed the next is how a two-minute setup
 * turns into three restarts.
 */

import type { SetupSnapshot } from "../../core/store";
import { type Locale, translate } from "../../i18n";
import { StatusText } from "../StatusText";
import { type StatusLine, sttStatus, ttsStatus } from "../status";
import type { OllamaState } from "../useOllamaState";

export function OllamaBlocked({
  setup,
  ollama,
  locale,
  onQuit,
}: {
  setup: SetupSnapshot;
  ollama: OllamaState;
  locale: Locale;
  onQuit: () => void;
}) {
  const otherLines = [ttsStatus(setup.tts, locale), sttStatus(setup.stt, locale)].filter(
    (line): line is StatusLine => line !== null,
  );
  const busy = ollama.checking || ollama.starting;

  return (
    <div className="panel panel--ollama" aria-live="polite">
      <h1 className="panel__title">
        {translate(
          locale,
          ollama.checking
            ? "setup.ollama.detected.title"
            : ollama.starting
              ? "setup.ollama.starting.title"
              : ollama.installedButStopped
                ? "setup.ollama.stopped.title"
                : "setup.ollama.missing.title",
        )}
      </h1>
      {busy ? (
        <>
          <div className="boot__spinner panel__ollama-spinner" aria-hidden="true" />
          <p className="panel__body">
            {translate(
              locale,
              ollama.starting ? "setup.ollama.starting.body" : "setup.ollama.checkingModel",
            )}
          </p>
          {ollama.starting && (
            <p className="panel__note">{translate(locale, "setup.ollama.autoCheck")}</p>
          )}
        </>
      ) : (
        <>
          <p className="panel__body">
            {ollama.installedButStopped
              ? translate(locale, "setup.ollama.stopped.body")
              : translate(locale, "setup.ollama.missing.body")}
          </p>
          {/* **Why Lumi needs it at all.** Only when it is not installed — someone who has
              it already is being asked to start it, not to be convinced. */}
          {!ollama.installedButStopped && (
            <p className="panel__note panel__ollama-note">
              <strong>{translate(locale, "setup.ollama.why.strong")}</strong>
              <br />
              {translate(locale, "setup.ollama.why.detail")}
            </p>
          )}
          <p className="panel__note">{translate(locale, "setup.ollama.autoCheck")}</p>
          {ollama.actionFailed && (
            <p className="panel__status panel__status--bad">
              {translate(locale, "setup.ollama.actionFailed")}
            </p>
          )}
          {!ollama.installedButStopped && (
            <div className="panel__actions panel__actions--single">
              <button type="button" className="panel__button" onClick={ollama.openSite}>
                {translate(locale, "setup.ollama.openSite")}
              </button>
            </div>
          )}
        </>
      )}
      {otherLines.map((line) => (
        <StatusText key={line.text} line={line} />
      ))}
      {/* **A way out that is not the tray.** Anyone stopped here has not met Lumi yet
          and does not know it lives there (ADR-034). */}
      <div className="panel__actions panel__actions--single panel__quit-action">
        <button type="button" className="panel__button" onClick={onQuit}>
          {translate(locale, "setup.quit")}
        </button>
      </div>
    </div>
  );
}
