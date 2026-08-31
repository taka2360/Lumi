import { describe, expect, it } from "vitest";

import { type CssRect, normalizeHitRects, sameHitRegion, toPhysicalRect } from "./geometry";

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

describe("sameHitRegion", () => {
  const rect = (x: number, y: number, width = 10, height = 10): CssRect => ({
    x,
    y,
    width,
    height,
  });

  it("treats an unchanged region as unchanged", () => {
    // The common case: a frame that moved nothing must not cost an IPC call.
    expect(sameHitRegion([rect(0, 0), rect(5, 5)], [rect(0, 0), rect(5, 5)])).toBe(true);
  });

  it("notices a rectangle that moved", () => {
    expect(sameHitRegion([rect(0, 0)], [rect(0, 1)])).toBe(false);
  });

  it("notices a rectangle that resized", () => {
    expect(sameHitRegion([rect(0, 0, 10, 10)], [rect(0, 0, 10, 11)])).toBe(false);
  });

  it("notices a rectangle appearing or disappearing", () => {
    // The palette opening adds one; the character being hidden removes one.
    expect(sameHitRegion([rect(0, 0)], [rect(0, 0), rect(5, 5)])).toBe(false);
    expect(sameHitRegion([rect(0, 0), rect(5, 5)], [rect(0, 0)])).toBe(false);
  });

  it("distinguishes regions that differ only in order", () => {
    // Order is stable in practice; treating a reorder as "same" would be a guess.
    expect(sameHitRegion([rect(0, 0), rect(5, 5)], [rect(5, 5), rect(0, 0)])).toBe(false);
  });

  it("treats two empty regions as the same", () => {
    // Nothing on screen twice running is not a change worth reporting.
    expect(sameHitRegion([], [])).toBe(true);
  });
});
