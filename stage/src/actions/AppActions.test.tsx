import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../i18n/provider";
import { AppActions } from "./AppActions";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const openCredits = vi.fn();
const quit = vi.fn();
let onCreditsError: ((error: unknown) => void) | undefined;
vi.mock("../platform/useStageShell", () => ({
  useOpenCredits: (onError?: (error: unknown) => void) => {
    onCreditsError = onError;
    return openCredits;
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
    quit.mockReset();
    onCreditsError = undefined;
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

  it("opens credits and licenses from the Stage window", () => {
    const buttons = render().querySelectorAll("button");

    expect(buttons[0]?.textContent).toBe("クレジットとライセンス");
    act(() => buttons[0]?.click());

    expect(openCredits).toHaveBeenCalledOnce();
  });

  it("quits Lumi from the Stage window", () => {
    const buttons = render().querySelectorAll("button");

    expect(buttons[1]?.textContent).toBe("終了");
    act(() => buttons[1]?.click());

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
    expect(consoleError).toHaveBeenCalledWith("Failed to open credits window", error);
    consoleError.mockRestore();
  });
});
