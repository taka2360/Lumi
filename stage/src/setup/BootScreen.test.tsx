import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type SetupSnapshot, UNKNOWN_SETUP } from "../core/store";
import { LocaleProvider } from "../i18n/provider";
import { BootScreen } from "./BootScreen";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
localStorage.setItem("lumi.locale", "ja");

describe("startup detail", () => {
  let root: ReturnType<typeof createRoot> | null = null;
  let container: HTMLDivElement | null = null;

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    container = null;
  });

  it("names STT model loading separately from engine startup", () => {
    const setup: SetupSnapshot = {
      ...UNKNOWN_SETUP,
      boot: "starting",
      tts: { ...UNKNOWN_SETUP.tts, state: "installed", runtime: "ready" },
      llm: { ...UNKNOWN_SETUP.llm, state: "detected", runtime: "ready" },
      stt: { ...UNKNOWN_SETUP.stt, state: "installed", runtime: "starting" },
    };
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root?.render(
        <LocaleProvider>
          <BootScreen setup={setup} connected />
        </LocaleProvider>,
      );
    });

    expect(container.textContent).toContain("音声認識モデルを読み込んでいます");
    expect(container.textContent).not.toContain("AivisSpeech を起動");
  });
});
