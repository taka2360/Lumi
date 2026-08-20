import { describe, expect, it } from "vitest";

import { hasMovedEnough, screenRectFromNdcPoints } from "./projection";

describe("screenRectFromNdcPoints", () => {
  it("the NDC center becomes the screen center", () => {
    const rect = screenRectFromNdcPoints(
      [
        { x: -0.5, y: 0.5 },
        { x: 0.5, y: -0.5 },
      ],
      400,
      800,
    );
    expect(rect).toEqual({ x: 100, y: 200, width: 200, height: 400 });
  });

  it("clips whatever spills off screen", () => {
    const rect = screenRectFromNdcPoints(
      [
        { x: -2, y: 2 },
        { x: 0, y: 0 },
      ],
      100,
      100,
    );
    expect(rect).toEqual({ x: 0, y: 0, width: 50, height: 50 });
  });

  it("no points means no region", () => {
    expect(screenRectFromNdcPoints([], 100, 100)).toBeNull();
  });

  it("entirely off-screen means no region (never a zero rect at the origin)", () => {
    const rect = screenRectFromNdcPoints(
      [
        { x: -3, y: 3 },
        { x: -2, y: 2 },
      ],
      100,
      100,
    );
    expect(rect).toBeNull();
  });
});

describe("hasMovedEnough", () => {
  const rect = { x: 10, y: 10, width: 50, height: 50 };

  it("never sends for a slight jitter", () => {
    expect(hasMovedEnough(rect, { ...rect, x: 10.4 })).toBe(false);
  });

  it("sends once moved by 1px or more", () => {
    expect(hasMovedEnough(rect, { ...rect, x: 11.5 })).toBe(true);
  });

  it("always sends when the region's presence changes", () => {
    expect(hasMovedEnough(rect, null)).toBe(true);
    expect(hasMovedEnough(null, rect)).toBe(true);
    expect(hasMovedEnough(null, null)).toBe(false);
  });
});
