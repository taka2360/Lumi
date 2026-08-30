import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ACTION_ITEMS } from "../actions/items";
import { Help } from "./Help";

vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);

describe("help window", () => {
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
    act(() => root?.render(<Help />));
    return container;
  }

  it("names the gesture that opens the action menu", () => {
    // ★ **The one gesture nothing on screen suggests** (ADR-047). This page and the tray
    // item beside it are the whole of how someone learns it; if the right click is not
    // written here, the palette is unreachable for anyone who has not guessed it.
    expect(render().textContent).toContain("右クリック");
  });

  it("explains every action the palette offers", () => {
    const text = render().textContent ?? "";

    // Both halves: the glyph the button carries, and the name beside it. A guide that
    // lists one without the other cannot be matched against the row on the character.
    for (const item of ACTION_ITEMS) {
      expect(text).toContain(item.glyph);
    }
    expect(text).toContain("設定");
    expect(text).toContain("終了");
  });

  it("names the tray as the way back when the menu cannot be found", () => {
    expect(render().textContent).toContain("タスクトレイ");
  });

  it("follows the locale the rest of Lumi is using", () => {
    localStorage.setItem("lumi.locale", "en");

    expect(render().textContent).toContain("Right click");
  });
});
