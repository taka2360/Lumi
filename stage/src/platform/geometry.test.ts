import { describe, expect, it } from "vitest";

import { normalizeHitRects, toPhysicalRect } from "./geometry";

describe("toPhysicalRect", () => {
  it("converts CSS pixels to physical pixels", () => {
    expect(toPhysicalRect({ x: 10, y: 20, width: 30, height: 40 }, 1.5)).toEqual({
      x: 15,
      y: 30,
      width: 45,
      height: 60,
    });
  });

  it("leaves values unchanged at 1x", () => {
    const rect = { x: 1, y: 2, width: 3, height: 4 };
    expect(toPhysicalRect(rect, 1)).toEqual(rect);
  });
});

describe("normalizeHitRects", () => {
  it("drops zero-area rectangles", () => {
    const rects = [
      { x: 0, y: 0, width: 0, height: 10 },
      { x: 0, y: 0, width: 10, height: 0 },
      { x: 0, y: 0, width: 10, height: 10 },
    ];
    expect(normalizeHitRects(rects, 1)).toHaveLength(1);
  });

  it("drops negative sizes", () => {
    expect(normalizeHitRects([{ x: 0, y: 0, width: -1, height: 10 }], 1)).toEqual([]);
  });
});
