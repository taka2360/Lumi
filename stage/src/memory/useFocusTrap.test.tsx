/**
 * The modal's keyboard behaviour.
 *
 * **This guards an irreversible action.** The dialog it was written for erases every
 * memory Lumi has; the failure modes here are "Enter went somewhere unexpected" and
 * "Tab reached the [forget] button on the list behind the dialog".
 */

import { act, useRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useFocusTrap } from "./useFocusTrap";

let container: HTMLDivElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
});

function Dialog({ active, onEscape }: { active: boolean; onEscape: () => void }) {
  const box = useRef<HTMLDivElement | null>(null);
  const cancel = useRef<HTMLButtonElement | null>(null);
  useFocusTrap(active, box, cancel, onEscape);
  return (
    <div ref={box}>
      <button type="button" id="erase">
        erase
      </button>
      <button type="button" ref={cancel} id="cancel">
        cancel
      </button>
    </div>
  );
}

function mount(active: boolean, onEscape = () => {}) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root?.render(<Dialog active={active} onEscape={onEscape} />));
}

const press = (key: string, shiftKey = false) =>
  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, shiftKey, bubbles: true }));
  });

describe("useFocusTrap", () => {
  it("puts focus on the safe button, not the destructive one", () => {
    mount(true);
    // **The default action is irreversible**, so the first Enter must not trigger it.
    expect(document.activeElement?.id).toBe("cancel");
  });

  it("closes on Escape", () => {
    const onEscape = vi.fn();
    mount(true, onEscape);
    press("Escape");
    expect(onEscape).toHaveBeenCalledOnce();
  });

  it("wraps Tab from the last control back to the first", () => {
    mount(true);
    document.querySelector<HTMLButtonElement>("#cancel")?.focus();
    press("Tab");
    // Tabbing out would land on the list behind the dialog, where [forget] sits.
    expect(document.activeElement?.id).toBe("erase");
  });

  it("wraps Shift+Tab from the first control back to the last", () => {
    mount(true);
    document.querySelector<HTMLButtonElement>("#erase")?.focus();
    press("Tab", true);
    expect(document.activeElement?.id).toBe("cancel");
  });

  it("does nothing while inactive", () => {
    const onEscape = vi.fn();
    mount(false, onEscape);
    press("Escape");
    expect(onEscape).not.toHaveBeenCalled();
    expect(document.activeElement?.id).not.toBe("cancel");
  });

  it("hands focus back to whatever opened it", () => {
    const opener = document.createElement("button");
    opener.id = "opener";
    document.body.appendChild(opener);
    opener.focus();

    mount(true);
    expect(document.activeElement?.id).toBe("cancel");

    act(() => root?.render(<Dialog active={false} onEscape={() => {}} />));
    // Otherwise the keyboard lands at the top of the page after the dialog closes.
    expect(document.activeElement?.id).toBe("opener");
    opener.remove();
  });
});
