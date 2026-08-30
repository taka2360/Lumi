import { describe, expect, it } from "vitest";

import { canStartWindowDrag, windowScaleFactor } from "./useStageShell";

/** A press over `target`, with no modifier held unless one is named. */
function press(button: number, target: EventTarget | null, altKey = false) {
  return { button, altKey, target };
}

describe("window drag surface", () => {
  it("reserves a plain left press for touching the character", () => {
    // ★ **ADR-047.** Petting, poking and click reactions are all left-button gestures.
    // If a left press still moved the window, adding them later would mean guessing which
    // of the two the user meant — and the window drag is handed to the OS on press.
    const character = document.createElement("div");

    expect(canStartWindowDrag(press(0, character))).toBe(false);
  });

  it("reserves it on the boot and setup surface too", () => {
    // ★ The loading and setup cards stand in for a character that cannot be shown yet.
    // A window whose gestures change depending on what Lumi happens to be doing is one
    // nobody can learn, so the same rule holds before the character appears.
    const panel = document.createElement("div");
    const title = document.createElement("h1");
    panel.appendChild(title);

    expect(canStartWindowDrag(press(0, panel))).toBe(false);
    expect(canStartWindowDrag(press(0, title))).toBe(false);
  });

  it("moves the window with alt or the middle button", () => {
    const surface = document.createElement("div");

    expect(canStartWindowDrag(press(0, surface, true))).toBe(true);
    expect(canStartWindowDrag(press(1, surface))).toBe(true);
  });

  it("does not steal presses from setup controls", () => {
    const panel = document.createElement("div");
    const button = document.createElement("button");
    const label = document.createElement("span");
    button.appendChild(label);
    panel.appendChild(button);

    // The exclusion wins over both gestures that would otherwise move the window.
    expect(canStartWindowDrag(press(1, button))).toBe(false);
    expect(canStartWindowDrag(press(1, label))).toBe(false);
    expect(canStartWindowDrag(press(0, button, true))).toBe(false);
  });

  it("keeps setup commands selectable", () => {
    const command = document.createElement("code");
    const text = document.createElement("span");
    command.dataset.windowGesture = "exclude";
    command.appendChild(text);

    expect(canStartWindowDrag(press(1, command))).toBe(false);
    expect(canStartWindowDrag(press(0, text, true))).toBe(false);
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

    expect(canStartWindowDrag(press(2, surface))).toBe(false);
    expect(canStartWindowDrag(press(2, surface, true))).toBe(false);
  });
});
