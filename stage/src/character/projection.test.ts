import { describe, expect, it } from "vitest";

import { hasMovedEnough, screenRectFromNdcPoints } from "./projection";

describe("screenRectFromNdcPoints", () => {
  it("NDC の中心が画面の中心になる", () => {
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

  it("画面外にはみ出した分を切り落とす", () => {
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

  it("点が無ければ領域なし", () => {
    expect(screenRectFromNdcPoints([], 100, 100)).toBeNull();
  });

  it("完全に画面外なら領域なし（原点のゼロ矩形にしない）", () => {
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

  it("わずかな揺れでは送らない", () => {
    expect(hasMovedEnough(rect, { ...rect, x: 10.4 })).toBe(false);
  });

  it("1px 以上動いたら送る", () => {
    expect(hasMovedEnough(rect, { ...rect, x: 11.5 })).toBe(true);
  });

  it("領域の有無が変わったら必ず送る", () => {
    expect(hasMovedEnough(rect, null)).toBe(true);
    expect(hasMovedEnough(null, rect)).toBe(true);
    expect(hasMovedEnough(null, null)).toBe(false);
  });
});
