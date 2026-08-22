import { describe, expect, it } from "vitest";

import { computeIdlePose } from "./idle";

describe("idle pose", () => {
  it("computes procedural breathing and body swaying", () => {
    const pose0 = computeIdlePose(0);
    expect(pose0.offsetY).toBeCloseTo(0);
    expect(pose0.tiltZ).toBeCloseTo(0);
    expect(pose0.breathScale).toBeCloseTo(1.0);

    const pose1 = computeIdlePose(1.0); // 1/4 point of 4-second breath period (sin(pi/2) = 1)
    expect(pose1.offsetY).toBeCloseTo(0.012);
    expect(pose1.breathScale).toBeCloseTo(1.004);
  });
});
