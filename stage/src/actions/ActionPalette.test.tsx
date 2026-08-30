import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../i18n/provider";
import { ActionPalette, clampPalettePosition, PALETTE_MARGIN } from "./ActionPalette";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
// jsdom has no ResizeObserver. The palette observes itself so it can re-clamp when the
// wheel resizes the window; nothing here changes size, so an inert one is enough.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    disconnect() {}
  },
);

vi.mock("../platform/useStageShell", () => ({
  useOpenCredits: () => () => {},
  useOpenHelp: () => () => {},
  useOpenPanel: () => () => {},
  useQuit: () => () => {},
  getPlatformShell: () => ({ setLocale: async () => {} }),
}));

describe("palette placement", () => {
  const box = { width: 160, height: 40 };
  const viewport = { width: 348, height: 522 };

  it("opens where the press landed", () => {
    expect(clampPalettePosition({ x: 100, y: 200 }, box, viewport)).toEqual({ x: 100, y: 200 });
  });

  it("keeps the palette inside the window near an edge", () => {
    // ★ The palette lives inside the Stage window, so clamping to the window is also what
    // keeps it on screen when Lumi is standing at the edge of a display. There is no
    // second, monitor-level clamp to do.
    const placed = clampPalettePosition({ x: 340, y: 515 }, box, viewport);

    expect(placed.x + box.width).toBeLessThanOrEqual(viewport.width - PALETTE_MARGIN);
    expect(placed.y + box.height).toBeLessThanOrEqual(viewport.height - PALETTE_MARGIN);
  });

  it("pins to the near edge when the window is narrower than the palette", () => {
    // Shell allows the Stage down to 160px wide. Centring the overflow would push the
    // first buttons off the left instead of the last off the right.
    expect(clampPalettePosition({ x: 120, y: 10 }, box, { width: 160, height: 240 }).x).toBe(
      PALETTE_MARGIN,
    );
  });
});

describe("action palette", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;
  let previousLocale: string | null;
  const onDismiss = vi.fn();
  const onMove = vi.fn();

  beforeEach(() => {
    previousLocale = localStorage.getItem("lumi.locale");
    localStorage.setItem("lumi.locale", "ja");
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    onDismiss.mockReset();
    onMove.mockReset();
    if (previousLocale === null) {
      localStorage.removeItem("lumi.locale");
    } else {
      localStorage.setItem("lumi.locale", previousLocale);
    }
  });

  function render(): HTMLDivElement {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root?.render(
        <LocaleProvider>
          <ActionPalette origin={{ x: 20, y: 30 }} onMove={onMove} onDismiss={onDismiss} />
        </LocaleProvider>,
      );
    });
    return container;
  }

  function layer(view: HTMLDivElement): HTMLElement {
    const found = view.querySelector<HTMLElement>(".palette-layer");
    if (!found) {
      throw new Error("palette layer is missing");
    }
    return found;
  }

  it("carries every application action", () => {
    // **The only way to reach them.** Nothing else on the character window opens these
    // any more (ADR-047), so a button missing here is a window nobody can open.
    const buttons = [...render().querySelectorAll("button")];

    expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
      "設定",
      "インスペクター",
      "記憶",
      "使いかた",
      "クレジットとライセンス",
      "終了",
    ]);
  });

  it("closes when the surface around it is left-clicked", () => {
    const view = render();

    act(() => {
      layer(view).dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0 }));
    });

    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("stays open when a press lands on the palette itself", () => {
    const view = render();
    const palette = view.querySelector(".palette");

    act(() => {
      palette?.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0 }));
    });

    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("re-anchors instead of closing when right-clicked again", () => {
    // ★ Dismissing on the right *press* would let the `contextmenu` that follows it
    // immediately reopen the palette — the two events are one gesture on Windows.
    const view = render();

    act(() => {
      layer(view).dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 2 }));
      layer(view).dispatchEvent(
        new MouseEvent("contextmenu", { bubbles: true, clientX: 60, clientY: 80 }),
      );
    });

    expect(onDismiss).not.toHaveBeenCalled();
    expect(onMove).toHaveBeenCalledWith({ x: 60, y: 80 });
  });

  it("never lets the WebView's own menu through", () => {
    const view = render();
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });

    act(() => {
      layer(view).dispatchEvent(event);
    });

    expect(event.defaultPrevented).toBe(true);
  });

  it("closes when a panel window takes the focus", () => {
    // Every action opens a window of its own. Leaving the palette on the character while
    // the user is in that window is the same as never having closed it.
    render();

    act(() => {
      window.dispatchEvent(new Event("blur"));
    });

    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("closes on Escape", () => {
    render();

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("keeps keyboard focus inside the palette", () => {
    const buttons = [...render().querySelectorAll("button")];
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (!first || !last) {
      throw new Error("palette actions are missing");
    }

    act(() => last.focus());
    const forwards = new KeyboardEvent("keydown", { key: "Tab", cancelable: true });
    act(() => window.dispatchEvent(forwards));
    expect(forwards.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);

    const backwards = new KeyboardEvent("keydown", {
      key: "Tab",
      shiftKey: true,
      cancelable: true,
    });
    act(() => window.dispatchEvent(backwards));
    expect(backwards.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(last);
  });
});
