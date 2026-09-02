/**
 * Keyboard behaviour for a modal dialog.
 *
 * **Escape closes it, focus starts inside, Tab stays inside, and focus goes back where it
 * came from.** Written for the erase-everything dialog, which is the one screen in Lumi
 * where the default action is irreversible — opening it with focus on nothing means the
 * first Enter goes wherever the browser decides.
 *
 * Kept as a hook rather than inlined because **the permission prompt (Phase 4a) is the
 * same problem**: a dialog that must be answered, over a window whose buttons must not be
 * reachable behind it.
 */

import { type RefObject, useEffect, useRef } from "react";

export function useFocusTrap(
  active: boolean,
  container: RefObject<HTMLElement | null>,
  initial: RefObject<HTMLElement | null>,
  onEscape: () => void,
): void {
  // **The trap is set up once per opening, not once per render.** Callers pass an inline
  // arrow, so `onEscape` is a new function on every render of the window behind the
  // dialog — and if the effect depended on it, each of those renders would tear the trap
  // down (handing focus back to the opener) and build it again (moving focus to Cancel).
  // Starting the erase re-renders the window; the keyboard must not jump back onto Cancel
  // while the irreversible request it would dismiss is still in flight.
  const latestEscape = useRef(onEscape);
  latestEscape.current = onEscape;

  useEffect(() => {
    if (!active) {
      return;
    }
    // Remembered before focus moves, so it can be handed back on close.
    const opener = document.activeElement as HTMLElement | null;
    initial.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        latestEscape.current();
        return;
      }
      if (event.key !== "Tab" || !container.current) {
        return;
      }
      // **Focus stays inside.** Tabbing out of a modal leaves the keyboard on the list
      // behind it, where [forget] sits — reachable by Enter, over a dialog that is
      // still covering the screen.
      const focusable = [...container.current.querySelectorAll<HTMLButtonElement>("button")];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) {
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      // Back to whatever opened it, so the keyboard does not land at the top of the page.
      opener?.focus();
    };
  }, [active, container, initial]);
}
