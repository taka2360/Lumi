import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../i18n/provider";
import { AppActions } from "./AppActions";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const openCredits = vi.fn();
const quit = vi.fn();
vi.mock("../platform/useStageShell", () => ({
  useOpenCredits: () => openCredits,
  useQuit: () => quit,
  getPlatformShell: () => ({ setLocale: async () => {} }),
}));

localStorage.setItem("lumi.locale", "ja");

describe("Stage application actions", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    openCredits.mockReset();
    quit.mockReset();
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
});
