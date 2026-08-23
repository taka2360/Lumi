import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../i18n/provider";
import { AppActions } from "./AppActions";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const openCredits = vi.fn();
const openPanel = vi.fn();
const quit = vi.fn();
let onCreditsError: ((error: unknown) => void) | undefined;
let onPanelError: ((error: unknown) => void) | undefined;
vi.mock("../platform/useStageShell", () => ({
  useOpenCredits: (onError?: (error: unknown) => void) => {
    onCreditsError = onError;
    return openCredits;
  },
  useOpenPanel: (kind: string, onError?: (error: unknown) => void) => {
    onPanelError = onError;
    return () => openPanel(kind);
  },
  useQuit: () => quit,
  getPlatformShell: () => ({ setLocale: async () => {} }),
}));

describe("Stage application actions", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;
  let previousLocale: string | null;

  beforeEach(() => {
    previousLocale = localStorage.getItem("lumi.locale");
    localStorage.setItem("lumi.locale", "ja");
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    openCredits.mockReset();
    openPanel.mockReset();
    quit.mockReset();
    onCreditsError = undefined;
    onPanelError = undefined;
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
          <AppActions />
        </LocaleProvider>,
      );
    });
    return container;
  }

  it("names every icon-only button", () => {
    // ★ **An icon with no accessible name is a button nobody can identify** — not by
    // screen reader, and not by hovering either. The glyph carries no meaning on its own,
    // which is exactly why the name has to come from i18n.
    const buttons = [...render().querySelectorAll("button")];

    expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
      "設定",
      "インスペクター",
      "記憶",
      "クレジットとライセンス",
      "終了",
    ]);
    for (const button of buttons) {
      // The tooltip and the accessible name are the same sentence, so a mouse user and a
      // screen-reader user are told the same thing.
      expect(button.getAttribute("title")).toBe(button.getAttribute("aria-label"));
      expect(button.textContent).not.toBe("");
    }
  });

  it("opens each panel window by name", () => {
    const buttons = render().querySelectorAll("button");

    act(() => buttons[0]?.click());
    act(() => buttons[1]?.click());
    act(() => buttons[2]?.click());

    // **The kind is fixed per button.** Shell knows these three and opens nothing else.
    expect(openPanel.mock.calls).toEqual([["settings"], ["inspector"], ["memory"]]);
  });

  it("opens credits and licenses from the Stage window", () => {
    const buttons = render().querySelectorAll("button");

    act(() => buttons[3]?.click());

    expect(openCredits).toHaveBeenCalledOnce();
  });

  it("quits Lumi from the Stage window", () => {
    const buttons = render().querySelectorAll("button");

    act(() => buttons[4]?.click());

    expect(quit).toHaveBeenCalledOnce();
  });

  it("shows a failure when Shell cannot open credits", () => {
    const view = render();
    const error = new Error("window unavailable");
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    act(() => onCreditsError?.(error));

    expect(view.querySelector('[role="alert"]')?.textContent).toBe(
      "クレジット画面を開けませんでした",
    );
    consoleError.mockRestore();
  });

  it("shows a failure when Shell cannot open a panel", () => {
    // **Never silently does nothing.** Shell logs an unknown kind and opens no window;
    // without this the button would look like it simply does not work.
    const view = render();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    act(() => onPanelError?.(new Error("window unavailable")));

    expect(view.querySelector('[role="alert"]')?.textContent).toBe("画面を開けませんでした");
    consoleError.mockRestore();
  });
});
