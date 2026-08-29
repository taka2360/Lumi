import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { updateSettings } from "../core/request";
import { type SettingsSnapshot, useStageStore } from "../core/store";
import { LocaleProvider } from "../i18n/provider";
import { Settings } from "./Settings";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
vi.mock("../core/request", () => ({ updateSettings: vi.fn() }));

function snapshot(value: string): SettingsSnapshot {
  return {
    version: 1,
    unreadable: false,
    values: { llm_model: { value, source: "file" } },
  };
}

describe("Settings", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
    useStageStore.setState({ settings: null });
    vi.mocked(updateSettings).mockReset();
  });

  it("resynchronizes a row when Core broadcasts a new snapshot", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      useStageStore.getState().setSettings(snapshot("old-model"));
      root?.render(
        <LocaleProvider>
          <Settings />
        </LocaleProvider>,
      );
    });

    expect(container.querySelector<HTMLInputElement>("input")?.value).toBe("old-model");

    act(() => {
      useStageStore.getState().setSettings(snapshot("new-model"));
    });

    expect(container.querySelector<HTMLInputElement>("input")?.value).toBe("new-model");
    expect(container.querySelector(".settings__src")?.textContent).toContain("Settings file");
  });

  it("shows the reading speed as an adjustable range", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      useStageStore.getState().setSettings({
        version: 1,
        unreadable: false,
        values: { tts_speed: { value: "1.4", source: "file" } },
      });
      root?.render(
        <LocaleProvider>
          <Settings />
        </LocaleProvider>,
      );
    });

    act(() => {
      container?.querySelector<HTMLButtonElement>("button")?.click();
    });

    const slider = container.querySelector<HTMLInputElement>('input[type="range"]');
    expect(slider?.value).toBe("1.4");
    expect(slider?.min).toBe("0.5");
    expect(slider?.max).toBe("2");
    expect(container.querySelector("output")?.textContent).toBe("1.4x");
  });

  it("shows the volume as a percentage, where the default reads 100%", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      useStageStore.getState().setSettings({
        version: 1,
        unreadable: false,
        values: { tts_volume: { value: "1.0", source: "default" } },
      });
      root?.render(
        <LocaleProvider>
          <Settings />
        </LocaleProvider>,
      );
    });

    act(() => {
      container?.querySelector<HTMLButtonElement>("button")?.click();
    });

    const slider = container.querySelector<HTMLInputElement>('input[type="range"]');
    expect(slider?.value).toBe("1.0");
    expect(slider?.min).toBe("0");
    expect(slider?.max).toBe("2");
    // **The Content Pack's own volume is 100%** (ADR-046), which is what makes the default
    // readable as "unchanged" rather than as some arbitrary level.
    expect(container.querySelector("output")?.textContent).toBe("100%");
  });

  it("reads an overridden volume the same way as an editable one", () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      useStageStore.getState().setSettings({
        version: 1,
        unreadable: false,
        values: { tts_volume: { value: "1.5", source: "env" } },
      });
      root?.render(
        <LocaleProvider>
          <Settings />
        </LocaleProvider>,
      );
    });

    act(() => {
      container?.querySelector<HTMLButtonElement>("button")?.click();
    });

    // **No slider — it is overridden and must stay uneditable.** But the number still has
    // to mean the same thing it does when editable: a multiplier, read as a percentage.
    expect(container.querySelector('input[type="range"]')).toBeNull();
    expect(container.querySelector(".settings__value")?.textContent).toBe("150%");
  });

  it("ignores an older save failure after a newer save starts", async () => {
    let rejectFirst: (reason: unknown) => void = () => {};
    let rejectSecond: (reason: unknown) => void = () => {};
    const first = new Promise<void>((_resolve, reject) => {
      rejectFirst = reject;
    });
    const second = new Promise<void>((_resolve, reject) => {
      rejectSecond = reject;
    });
    vi.mocked(updateSettings).mockReturnValueOnce(first).mockReturnValueOnce(second);

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      useStageStore.getState().setSettings({
        version: 1,
        unreadable: false,
        values: { tts_speed: { value: "1.2", source: "file" } },
      });
      root?.render(
        <LocaleProvider>
          <Settings />
        </LocaleProvider>,
      );
    });

    act(() => {
      container?.querySelector<HTMLButtonElement>("button")?.click();
    });

    const slider = container.querySelector<HTMLInputElement>('input[type="range"]');
    expect(slider).not.toBeNull();
    if (!slider) {
      throw new Error("speed slider was not rendered");
    }
    const commit = (value: string) => {
      slider.value = value;
      act(() => {
        slider.dispatchEvent(new Event("pointerup", { bubbles: true }));
      });
    };

    commit("1.3");
    commit("1.4");
    expect(updateSettings).toHaveBeenCalledTimes(2);

    await act(async () => {
      rejectFirst(new Error("old refusal"));
      await Promise.resolve();
    });
    expect(slider?.value).toBe("1.4");
    expect(container.querySelector(".settings__src")?.textContent).toContain("Settings file");

    act(() => {
      useStageStore.getState().setSettings({
        version: 1,
        unreadable: false,
        values: { tts_speed: { value: "1.4", source: "file" } },
      });
    });

    await act(async () => {
      rejectSecond(new Error("latest refusal"));
      await Promise.resolve();
    });
    expect(slider?.value).toBe("1.4");
    expect(container.querySelector(".settings__src")?.textContent).toContain("Settings file");
  });
});
