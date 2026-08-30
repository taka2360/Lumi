/**
 * The application actions, in the order they are shown.
 *
 * **One list, two readers.** `AppActions` renders the buttons and the help window
 * describes them; if each kept its own copy, the glyph on the button and the glyph in
 * the explanation of that button would eventually differ, and the explanation is the
 * only place the glyph is ever spelled out.
 *
 * **The glyphs are not translated.** A symbol has no Japanese and no English, and
 * putting it through i18n would create a string that must not differ per locale but
 * technically can. The names beside them do go through i18n.
 */

export type ActionName = "settings" | "inspector" | "memory" | "help" | "credits" | "quit";

export interface ActionItem {
  name: ActionName;
  glyph: string;
  /** The button's accessible name. */
  labelKey: `actions.${ActionName}`;
  /** One line on what it opens. Only the help window shows this. */
  aboutKey: `help.action.${ActionName}`;
}

export const ACTION_ITEMS: readonly ActionItem[] = [
  { name: "settings", glyph: "⚙", labelKey: "actions.settings", aboutKey: "help.action.settings" },
  {
    name: "inspector",
    glyph: "◎",
    labelKey: "actions.inspector",
    aboutKey: "help.action.inspector",
  },
  { name: "memory", glyph: "✿", labelKey: "actions.memory", aboutKey: "help.action.memory" },
  { name: "help", glyph: "?", labelKey: "actions.help", aboutKey: "help.action.help" },
  { name: "credits", glyph: "ⓘ", labelKey: "actions.credits", aboutKey: "help.action.credits" },
  { name: "quit", glyph: "⏻", labelKey: "actions.quit", aboutKey: "help.action.quit" },
];

/** The glyph for one action. **Throws rather than rendering a blank button.** */
export function actionGlyph(name: ActionName): string {
  const item = ACTION_ITEMS.find((candidate) => candidate.name === name);
  if (!item) {
    throw new Error(`Unknown application action: ${name}`);
  }
  return item.glyph;
}
