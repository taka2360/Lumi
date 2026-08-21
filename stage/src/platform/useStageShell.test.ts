import { describe, expect, it } from "vitest";

import { canStartWindowDrag, windowScaleFactor } from "./useStageShell";

describe("window drag surface", () => {
  it("lets the boot and setup surface move the window", () => {
    const panel = document.createElement("div");
    const title = document.createElement("h1");
    panel.appendChild(title);

    expect(canStartWindowDrag(0, panel)).toBe(true);
    expect(canStartWindowDrag(0, title)).toBe(true);
  });

  it("does not steal presses from setup controls", () => {
    const panel = document.createElement("div");
    const button = document.createElement("button");
    const label = document.createElement("span");
    button.appendChild(label);
    panel.appendChild(button);

    expect(canStartWindowDrag(0, button)).toBe(false);
    expect(canStartWindowDrag(0, label)).toBe(false);
  });

  it("keeps setup commands selectable", () => {
    const command = document.createElement("code");
    const text = document.createElement("span");
    command.dataset.windowGesture = "exclude";
    command.appendChild(text);

    expect(canStartWindowDrag(0, command)).toBe(false);
    expect(canStartWindowDrag(0, text)).toBe(false);
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

  it("reserves non-left presses for their native behavior", () => {
    expect(canStartWindowDrag(1, document.createElement("div"))).toBe(false);
    expect(canStartWindowDrag(2, document.createElement("div"))).toBe(false);
  });
});
