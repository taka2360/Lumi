/**
 * Lip sync. **Pure functions, so it needs neither three nor WS.**
 *
 * docs/interfaces/renderer.md test table 7 "mouth closes on silence"
 */

import { describe, expect, it } from "vitest";

import {
  advanceMouth,
  isMouthClosed,
  MOUTH_CLOSED,
  type MouthWeights,
  parseTimeline,
  TAIL_MS,
  type VisemeTimeline,
  visemeAt,
} from "./lipsync";

const TIMELINE: VisemeTimeline = {
  spans: [
    { viseme: "O", startMs: 100, durationMs: 150 },
    { viseme: null, startMs: 250, durationMs: 70 }, // "N"
    { viseme: "I", startMs: 320, durationMs: 120 },
  ],
  totalMs: 540,
};

/** Advances by `ms` milliseconds at 60fps. */
function run(target: Parameters<typeof advanceMouth>[1], ms: number): MouthWeights {
  let weights = MOUTH_CLOSED;
  for (let elapsed = 0; elapsed < ms; elapsed += 16) {
    weights = advanceMouth(weights, target, 0.016);
  }
  return weights;
}

describe("visemeAt", () => {
  it("returns the mouth shape for that time", () => {
    expect(visemeAt(TIMELINE, 150)).toBe("O");
    expect(visemeAt(TIMELINE, 380)).toBe("I");
  });

  it("closes the mouth before and after the utterance", () => {
    expect(visemeAt(TIMELINE, 0)).toBeNull();
    expect(visemeAt(TIMELINE, 99)).toBeNull();
    expect(visemeAt(TIMELINE, 5000)).toBeNull();
  });

  it("closes the mouth during moraic-nasal/geminate segments", () => {
    expect(visemeAt(TIMELINE, 260)).toBeNull();
  });

  it("moves to the next shape at a span boundary (never stays open across a gap)", () => {
    expect(visemeAt(TIMELINE, 250)).toBeNull();
    expect(visemeAt(TIMELINE, 320)).toBe("I");
  });

  it("closes once the grace period passes, even without `ended` arriving", () => {
    // **The mouth never stays open forever even if Core crashes** (fail-closed).
    expect(visemeAt(TIMELINE, TIMELINE.totalMs + TAIL_MS + 1)).toBeNull();
  });
});

describe("advanceMouth", () => {
  it("opens only the target vowel", () => {
    const weights = run("A", 200);
    expect(weights.A).toBeGreaterThan(0.9);
    expect(weights.I).toBe(0);
    expect(weights.O).toBe(0);
  });

  it("opens fast, closes slowly (asymmetric smoothing)", () => {
    const opening = run("A", 40).A;
    let closing = run("A", 300);
    for (let elapsed = 0; elapsed < 40; elapsed += 16) {
      closing = advanceMouth(closing, null, 0.016);
    }
    // Over the same 40ms, the amount opened exceeds the amount closed
    expect(opening).toBeGreaterThan(1 - closing.A);
  });

  it("fully closes on silence (no infinitesimal value lingers)", () => {
    let weights = run("A", 300);
    for (let elapsed = 0; elapsed < 1000; elapsed += 16) {
      weights = advanceMouth(weights, null, 0.016);
    }
    expect(isMouthClosed(weights)).toBe(true);
  });

  it("keeps weights within 0..1", () => {
    const weights = run("E", 5000);
    expect(weights.E).toBeLessThanOrEqual(1);
    expect(weights.E).toBeGreaterThanOrEqual(0);
  });
});

describe("parseTimeline", () => {
  it("reads Core's payload", () => {
    const timeline = parseTimeline({
      total_ms: 540,
      spans: [{ viseme: "O", start_ms: 100, duration_ms: 150 }],
    });
    expect(timeline).toEqual({
      totalMs: 540,
      spans: [{ viseme: "O", startMs: 100, durationMs: 150 }],
    });
  });

  it("falls back an unknown viseme to closing the mouth", () => {
    const timeline = parseTimeline({
      total_ms: 10,
      spans: [{ viseme: "X", start_ms: 0, duration_ms: 10 }],
    });
    expect(timeline?.spans[0]?.viseme).toBeNull();
  });

  it("returns null when malformed (moves nothing)", () => {
    expect(parseTimeline({})).toBeNull();
    expect(parseTimeline({ total_ms: 10 })).toBeNull();
    expect(parseTimeline({ total_ms: -1, spans: [] })).toBeNull();
    expect(parseTimeline({ total_ms: 10, spans: [{ start_ms: "x", duration_ms: 1 }] })).toBeNull();
    expect(parseTimeline({ total_ms: 10, spans: [null] })).toBeNull();
  });
});
