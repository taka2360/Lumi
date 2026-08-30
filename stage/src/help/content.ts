/**
 * What the help window lists. **Keys, not sentences** — the wording lives in `../i18n`
 * so this page changes language with the rest of Lumi.
 *
 * The action rows are not here: they come from `../actions/items.ts`, which is also what
 * builds the buttons themselves. Describing a button from a second list is how the glyph
 * in the explanation ends up differing from the glyph on the button.
 */

import type { MessageKey } from "../i18n";

export interface GestureRow {
  gesture: MessageKey;
  effect: MessageKey;
}

/**
 * The mouse gestures, in the order they matter to someone who has just met Lumi
 * (ADR-047). **Right click comes second**: it is the one gesture nothing on screen
 * suggests, and everything else is reachable once it is known.
 */
export const GESTURES: readonly GestureRow[] = [
  { gesture: "help.gesture.touch", effect: "help.gesture.touch.effect" },
  { gesture: "help.gesture.menu", effect: "help.gesture.menu.effect" },
  { gesture: "help.gesture.scale", effect: "help.gesture.scale.effect" },
  { gesture: "help.gesture.move", effect: "help.gesture.move.effect" },
];
