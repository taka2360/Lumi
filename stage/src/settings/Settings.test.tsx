import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type SettingsSnapshot, useStageStore } from "../core/store";
import { LocaleProvider } from "../i18n/provider";
import { Settings } from "./Settings";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

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

    act(() => {
      container?.querySelector<HTMLButtonElement>("button")?.click();
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
});
