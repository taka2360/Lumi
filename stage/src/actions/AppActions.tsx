/** User-invoked application actions available directly from the Stage window. */

import { useCallback, useState } from "react";

import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { useOpenCredits, useQuit } from "../platform/useStageShell";

export function AppActions() {
  const locale = useLocale();
  const [creditsError, setCreditsError] = useState(false);
  const onCreditsError = useCallback(() => setCreditsError(true), []);
  const openCredits = useOpenCredits(onCreditsError);
  const quit = useQuit();
  const handleOpenCredits = useCallback(() => {
    setCreditsError(false);
    openCredits();
  }, [openCredits]);

  return (
    <nav className="app-actions" aria-label={translate(locale, "actions.menu")}>
      <button type="button" className="app-actions__button" onClick={handleOpenCredits}>
        {translate(locale, "actions.credits")}
      </button>
      <button type="button" className="app-actions__button" onClick={quit}>
        {translate(locale, "actions.quit")}
      </button>
      {creditsError && (
        <span className="app-actions__error" role="alert">
          {translate(locale, "actions.creditsFailed")}
        </span>
      )}
    </nav>
  );
}
