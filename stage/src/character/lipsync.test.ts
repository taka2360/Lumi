/**
 * リップシンク。**純粋関数なので、three も WS も要らない。**
 *
 * docs/interfaces/renderer.md テスト表 7「無音時に口を閉じる」
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
    { viseme: null, startMs: 250, durationMs: 70 }, // 「ン」
    { viseme: "I", startMs: 320, durationMs: 120 },
  ],
  totalMs: 540,
};

/** 60fps で `ms` ミリ秒ぶん進める。 */
function run(target: Parameters<typeof advanceMouth>[1], ms: number): MouthWeights {
  let weights = MOUTH_CLOSED;
  for (let elapsed = 0; elapsed < ms; elapsed += 16) {
    weights = advanceMouth(weights, target, 0.016);
  }
  return weights;
}

describe("visemeAt", () => {
  it("その時刻の口の形を返す", () => {
    expect(visemeAt(TIMELINE, 150)).toBe("O");
    expect(visemeAt(TIMELINE, 380)).toBe("I");
  });

  it("発話の前後では口を閉じる", () => {
    expect(visemeAt(TIMELINE, 0)).toBeNull();
    expect(visemeAt(TIMELINE, 99)).toBeNull();
    expect(visemeAt(TIMELINE, 5000)).toBeNull();
  });

  it("撥音・促音の区間では口を閉じる", () => {
    expect(visemeAt(TIMELINE, 260)).toBeNull();
  });

  it("span の切れ目で次の形に移る（隙間で開きっぱなしにならない）", () => {
    expect(visemeAt(TIMELINE, 250)).toBeNull();
    expect(visemeAt(TIMELINE, 320)).toBe("I");
  });

  it("ended が来なくても、猶予を過ぎたら閉じる", () => {
    // **Core が落ちても口が開きっぱなしにならない**（fail-closed）。
    expect(visemeAt(TIMELINE, TIMELINE.totalMs + TAIL_MS + 1)).toBeNull();
  });
});

describe("advanceMouth", () => {
  it("目標の母音だけが開く", () => {
    const weights = run("A", 200);
    expect(weights.A).toBeGreaterThan(0.9);
    expect(weights.I).toBe(0);
    expect(weights.O).toBe(0);
  });

  it("開くのが速く、閉じるのが遅い（非対称スムージング）", () => {
    const opening = run("A", 40).A;
    let closing = run("A", 300);
    for (let elapsed = 0; elapsed < 40; elapsed += 16) {
      closing = advanceMouth(closing, null, 0.016);
    }
    // 同じ 40ms で、開く量の方が、閉じる量より大きい
    expect(opening).toBeGreaterThan(1 - closing.A);
  });

  it("無音になると完全に閉じる（微小値を残さない）", () => {
    let weights = run("A", 300);
    for (let elapsed = 0; elapsed < 1000; elapsed += 16) {
      weights = advanceMouth(weights, null, 0.016);
    }
    expect(isMouthClosed(weights)).toBe(true);
  });

  it("重みが 0..1 に収まる", () => {
    const weights = run("E", 5000);
    expect(weights.E).toBeLessThanOrEqual(1);
    expect(weights.E).toBeGreaterThanOrEqual(0);
  });
});

describe("parseTimeline", () => {
  it("Core の payload を読む", () => {
    const timeline = parseTimeline({
      total_ms: 540,
      spans: [{ viseme: "O", start_ms: 100, duration_ms: 150 }],
    });
    expect(timeline).toEqual({
      totalMs: 540,
      spans: [{ viseme: "O", startMs: 100, durationMs: 150 }],
    });
  });

  it("知らないビセームは「口を閉じる」にする", () => {
    const timeline = parseTimeline({
      total_ms: 10,
      spans: [{ viseme: "X", start_ms: 0, duration_ms: 10 }],
    });
    expect(timeline?.spans[0]?.viseme).toBeNull();
  });

  it("壊れていたら null（何も動かさない）", () => {
    expect(parseTimeline({})).toBeNull();
    expect(parseTimeline({ total_ms: 10 })).toBeNull();
    expect(parseTimeline({ total_ms: -1, spans: [] })).toBeNull();
    expect(parseTimeline({ total_ms: 10, spans: [{ start_ms: "x", duration_ms: 1 }] })).toBeNull();
    expect(parseTimeline({ total_ms: 10, spans: [null] })).toBeNull();
  });
});
