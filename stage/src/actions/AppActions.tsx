/** User-invoked application actions available directly from the Stage window. */

import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { useOpenCredits, useQuit } from "../platform/useStageShell";

export function AppActions() {
  const locale = useLocale();
  const openCredits = useOpenCredits();
  const quit = useQuit();

  return (
    <nav className="app-actions" aria-label={translate(locale, "actions.menu")}>
      <button type="button" className="app-actions__button" onClick={openCredits}>
        {translate(locale, "actions.credits")}
      </button>
      <button type="button" className="app-actions__button" onClick={quit}>
        {translate(locale, "actions.quit")}
      </button>
    </nav>
  );
}
