/**
 * Mounting a Lumi window. **The one piece every entry point repeats.**
 *
 * Each window is its own page with its own entry module (`vite.config.ts`), and all four
 * of them found `#root`, threw the same error when it was missing, and rendered into it
 * inside `StrictMode`. Four copies of that is four places to forget the same thing.
 *
 * **This module deliberately knows nothing about Core or about locale.** `credits` and
 * `help` must not load the code that connects to Core (docs/architecture/ui.md §1, held
 * in place by `credits/content.test.ts`), so anything shared by every entry has to stay
 * this side of that line. Which locale mechanism a window uses — `LocaleProvider` for the
 * windows that talk to Core, `useStandaloneLocale` for the two that do not — stays in the
 * entry, because that is exactly where the windows genuinely differ.
 */

import type { ReactNode } from "react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

/**
 * The element every page mounts into.
 *
 * **Fails loudly.** A page whose markup lost `#root` would otherwise render nothing at
 * all, which looks like a hung window rather than a mistake in the HTML.
 */
export function rootElement(): HTMLElement {
  const container = document.getElementById("root");
  if (!container) {
    throw new Error("Root element #root not found");
  }
  return container;
}

/**
 * Renders `children` into the page's root.
 *
 * Takes the container as an argument for the panel entry, which has to read an attribute
 * off it before it knows what to render — passing it back avoids looking it up twice.
 */
export function mountRoot(children: ReactNode, container: HTMLElement = rootElement()): void {
  createRoot(container).render(<StrictMode>{children}</StrictMode>);
}
