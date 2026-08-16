import { describe, expect, it } from "vitest";

import { normalizeHitRects, toPhysicalRect } from "./geometry";

describe("toPhysicalRect", () => {
  it("CSS ピクセルを物理ピクセルに変換する", () => {
    expect(toPhysicalRect({ x: 10, y: 20, width: 30, height: 40 }, 1.5)).toEqual({
      x: 15,
      y: 30,
      width: 45,
      height: 60,
    });
  });

  it("等倍のときは値を変えない", () => {
    const rect = { x: 1, y: 2, width: 3, height: 4 };
    expect(toPhysicalRect(rect, 1)).toEqual(rect);
  });
});

describe("normalizeHitRects", () => {
  it("面積ゼロの矩形を落とす", () => {
    const rects = [
      { x: 0, y: 0, width: 0, height: 10 },
      { x: 0, y: 0, width: 10, height: 0 },
      { x: 0, y: 0, width: 10, height: 10 },
    ];
    expect(normalizeHitRects(rects, 1)).toHaveLength(1);
  });

  it("負のサイズを落とす", () => {
    expect(normalizeHitRects([{ x: 0, y: 0, width: -1, height: 10 }], 1)).toEqual([]);
  });
});
