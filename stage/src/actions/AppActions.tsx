/**
 * The row of application actions on the character window (docs/architecture/ui.md §1).
 *
 * ## Why these are icons and not words
 *
 * They sit on top of the character. With words, **the row of buttons ends up bigger than
 * Lumi** — five labels are wider than the window they float over, and the thing the window
 * exists to show is the one thing they cover. An icon fits at a size that can stay on
 * screen; what it means is carried by `aria-label` and `title`, both translated.
 *
 * **The glyphs themselves are not translated.** A symbol has no Japanese and no English,
 * and putting it through i18n would create a string that must not differ per locale but
 * technically can. They live in `items.ts`, which the help window reads as well.
 */

import { useCallback, useMemo, useState } from "react";

import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { useOpenCredits, useOpenHelp, useOpenPanel, useQuit } from "../platform/useStageShell";
import { actionGlyph } from "./items";

function ActionButton({
  glyph,
  labelKey,
  onClick,
}: {
  glyph: string;
  labelKey:
    | "actions.settings"
    | "actions.inspector"
    | "actions.memory"
    | "actions.help"
    | "actions.credits"
    | "actions.quit";
  onClick: () => void;
}) {
  const locale = useLocale();
  const label = translate(locale, labelKey);
  return (
    <button
      type="button"
      className="app-actions__button"
      onClick={onClick}
      // **Both.** `title` is what a mouse user gets, `aria-label` is what a screen reader
      // gets, and an icon-only button with neither is a button with no name at all.
      title={label}
      aria-label={label}
    >
      <span aria-hidden="true">{glyph}</span>
    </button>
  );
}

type FailureKey = "actions.openFailed" | "actions.creditsFailed";

export function AppActions() {
  const locale = useLocale();
  const [failed, setFailed] = useState<FailureKey | null>(null);
  const report = useCallback((key: FailureKey) => {
    return (error: unknown) => {
      // Keep the underlying Shell failure available for diagnosing a failed invoke.
      // biome-ignore lint/suspicious/noConsole: Stage has no shared telemetry sink yet.
      console.error("Failed to open a Lumi window", error);
      setFailed(key);
    };
  }, []);
  const onOpenError = useMemo(() => report("actions.openFailed"), [report]);
  const onCreditsError = useMemo(() => report("actions.creditsFailed"), [report]);

  // **One hook per button, in a fixed order.** Building them in a loop would make the
  // hook order depend on the list, which React does not allow.
  const openSettings = useOpenPanel("settings", onOpenError);
  const openInspector = useOpenPanel("inspector", onOpenError);
  const openMemory = useOpenPanel("memory", onOpenError);
  const openCredits = useOpenCredits(onCreditsError);
  const openHelp = useOpenHelp(onOpenError);
  const quit = useQuit();

  const clearThen = (open: () => void) => () => {
    setFailed(null);
    open();
  };

  return (
    <nav className="app-actions" aria-label={translate(locale, "actions.menu")}>
      <ActionButton
        glyph={actionGlyph("settings")}
        labelKey="actions.settings"
        onClick={clearThen(openSettings)}
      />
      <ActionButton
        glyph={actionGlyph("inspector")}
        labelKey="actions.inspector"
        onClick={clearThen(openInspector)}
      />
      <ActionButton
        glyph={actionGlyph("memory")}
        labelKey="actions.memory"
        onClick={clearThen(openMemory)}
      />
      <ActionButton
        glyph={actionGlyph("help")}
        labelKey="actions.help"
        onClick={clearThen(openHelp)}
      />
      <ActionButton
        glyph={actionGlyph("credits")}
        labelKey="actions.credits"
        onClick={clearThen(openCredits)}
      />
      <ActionButton glyph={actionGlyph("quit")} labelKey="actions.quit" onClick={quit} />
      {failed && (
        <span className="app-actions__error" role="alert">
          {translate(locale, failed)}
        </span>
      )}
    </nav>
  );
}
