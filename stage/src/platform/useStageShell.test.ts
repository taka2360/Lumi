import { describe, expect, it } from "vitest";

import { canStartWindowDrag, windowScaleFactor } from "./useStageShell";

/** A press with no modifier held, over `target`. */
function press(button: number, target: EventTarget | null, altKey = false) {
  return { button, altKey, target };
}

describe("window drag surface", () => {
  it("lets the boot and setup surface move the window with a plain left press", () => {
    const panel = document.createElement("div");
    const title = document.createElement("h1");
    panel.appendChild(title);

    expect(canStartWindowDrag(press(0, panel), "panel")).toBe(true);
    expect(canStartWindowDrag(press(0, title), "panel")).toBe(true);
  });

  it("reserves a plain left press on the character for touching it", () => {
    // ★ **ADR-047.** Petting, poking and click reactions are all left-button gestures.
    // If a left press still moved the window, adding them later would mean guessing which
    // of the two the user meant — and the window drag is handed to the OS on press.
    const character = document.createElement("div");

    expect(canStartWindowDrag(press(0, character), "character")).toBe(false);
  });

  it("moves the window from the character with alt or the middle button", () => {
    const character = document.createElement("div");

    expect(canStartWindowDrag(press(0, character, true), "character")).toBe(true);
    expect(canStartWindowDrag(press(1, character), "character")).toBe(true);
  });

  it("does not steal presses from setup controls", () => {
    const panel = document.createElement("div");
    const button = document.createElement("button");
    const label = document.createElement("span");
    button.appendChild(label);
    panel.appendChild(button);

    expect(canStartWindowDrag(press(0, button), "panel")).toBe(false);
    expect(canStartWindowDrag(press(0, label), "panel")).toBe(false);
    // The exclusion wins over the gestures that are allowed on the character too.
    expect(canStartWindowDrag(press(1, button), "character")).toBe(false);
    expect(canStartWindowDrag(press(0, button, true), "character")).toBe(false);
  });

  it("keeps setup commands selectable", () => {
    const command = document.createElement("code");
    const text = document.createElement("span");
    command.dataset.windowGesture = "exclude";
    command.appendChild(text);

    expect(canStartWindowDrag(press(0, command), "panel")).toBe(false);
    expect(canStartWindowDrag(press(0, text), "panel")).toBe(false);
  });

  it("does not resize for wheel events over setup controls or commands", () => {
    const button = document.createElement("button");
    const command = document.createElement("code");
    command.dataset.windowGesture = "exclude";

    expect(windowScaleFactor(-1, button)).toBeNull();
    expect(windowScaleFactor(1, command)).toBeNull();
  });

  it("resizes for wheel events over the boot and setup surface", () => {
    const surface = document.createElement("div");

    expect(windowScaleFactor(-1, surface)).toBeGreaterThan(1);
    expect(windowScaleFactor(1, surface)).toBeLessThan(1);
  });

  it("leaves the right button to the action palette", () => {
    // Right presses open the palette (ADR-047); starting a drag from one would move the
    // window out from under the menu that is about to appear.
    const surface = document.createElement("div");

    expect(canStartWindowDrag(press(2, surface), "panel")).toBe(false);
    expect(canStartWindowDrag(press(2, surface, true), "character")).toBe(false);
  });
});
