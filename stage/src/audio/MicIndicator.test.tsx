import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useStageStore } from "../core/store";
import { LocaleProvider } from "../i18n/provider";
import { MicIndicator } from "./MicIndicator";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

const setMicMuted = vi.fn();
vi.mock("../core/useCoreConnection", () => ({
  setMicMuted: (muted: boolean) => setMicMuted(muted),
}));
vi.mock("../platform/useStageShell", () => ({
  getPlatformShell: () => ({ setLocale: async () => {} }),
}));

describe("microphone indicator", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  beforeEach(() => {
    localStorage.setItem("lumi.locale", "ja");
    setMicMuted.mockResolvedValue(undefined);
  });

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    setMicMuted.mockReset();
    useStageStore.setState({ mic: null });
  });

  function render(): HTMLDivElement {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root?.render(
        <LocaleProvider>
          <MicIndicator />
        </LocaleProvider>,
      );
    });
    return container;
  }

  it("says nothing until Core has said something", () => {
    // ★ **"Core has not spoken" is not "the microphone is closed."** Drawing a confident
    // "not listening" during boot would be a claim nobody made, and it is the claim a user
    // would most reasonably act on.
    expect(render().querySelector("button")).toBeNull();
  });

  it("does not change on the press, only on what Core says", () => {
    // ★ The one thing in this feature that must not be optimistic. A light that goes out
    // when the button is pressed tells the user the room is private before it is.
    const view = render();
    act(() => useStageStore.getState().setMic({ open: true, muted: false }));
    expect(view.querySelector("button")?.className).toContain("mic--open");

    act(() => view.querySelector("button")?.click());

    expect(setMicMuted).toHaveBeenCalledWith(true);
    expect(view.querySelector("button")?.className).toContain("mic--open");

    act(() => useStageStore.getState().setMic({ open: false, muted: true }));
    expect(view.querySelector("button")?.className).toContain("mic--muted");
  });

  it("★ does not call a closed microphone open", () => {
    // `{open: false, muted: false}` is a real answer from Core — no capture device, or
    // listening has not started. **Folding it into "open" puts a live microphone icon on
    // screen for a microphone that is not there**, which is the one claim this control
    // exists to make truthfully.
    const view = render();
    act(() => useStageStore.getState().setMic({ open: false, muted: false }));

    const button = view.querySelector("button");
    expect(button?.className).toContain("mic--off");
    expect(button?.className).not.toContain("mic--open");
    expect(button?.getAttribute("aria-label")).toBe("マイクは使えません");
    // Nothing to mute, so nothing to press. Core refuses it anyway.
    expect(button?.disabled).toBe(true);
    act(() => button?.click());
    expect(setMicMuted).not.toHaveBeenCalled();
  });

  it("carries a translated name, not just a glyph", () => {
    const view = render();
    act(() => useStageStore.getState().setMic({ open: true, muted: false }));

    expect(view.querySelector("button")?.getAttribute("aria-label")).toBe("ミュートする");
    expect(view.querySelector("button")?.getAttribute("aria-pressed")).toBe("false");
  });
});
