/**
 * Expressions. **The Renderer's fallback is the point being tested.**
 *
 * Design → docs/interfaces/renderer.md / ADR-009
 *
 * Core sends nine emotions; VRM defines six presets. **Core is never allowed to know
 * that** — so what happens to the other three lives here, and nowhere else.
 */

import { describe, expect, it } from "vitest";

import {
  advanceExpression,
  EMOTIONS,
  EXPRESSION_NEUTRAL,
  type ExpressionIntent,
  type ExpressionState,
  type ExpressionWeights,
  parseExpression,
  targetWeights,
  VRM_PRESETS,
  type VrmPreset,
} from "./expression";

function intent(over: Partial<ExpressionIntent> = {}): ExpressionIntent {
  return { emotion: "happy", intensity: 0.7, blendMs: 200, durationMs: null, ...over };
}

function state(over: Partial<ExpressionIntent> = {}, startedAtMs = 0): ExpressionState {
  return { intent: intent(over), startedAtMs };
}

/** The preset carrying any weight, and how much. */
function applied(weights: ExpressionWeights): [VrmPreset, number] | null {
  const found = VRM_PRESETS.find((preset) => weights[preset] > 0);
  return found === undefined ? null : [found, weights[found]];
}

describe("mapping an intent onto a preset", () => {
  it("every emotion resolves to exactly one preset", () => {
    // **Never leaves an emotion unmapped.** A missing entry would render as no
    // expression at all, which looks identical to "expressions aren't wired up".
    for (const emotion of EMOTIONS) {
      const weights = targetWeights(state({ emotion, intensity: 1 }), 0);
      const found = VRM_PRESETS.filter((preset) => weights[preset] > 0);
      expect(found.length, `${emotion} did not resolve to a preset`).toBeLessThanOrEqual(1);
    }
  });

  it("emotions VRM has go straight through", () => {
    expect(applied(targetWeights(state({ emotion: "happy", intensity: 0.5 }), 0))).toEqual([
      "happy",
      0.5,
    ]);
    expect(applied(targetWeights(state({ emotion: "angry", intensity: 0.5 }), 0))).toEqual([
      "angry",
      0.5,
    ]);
  });

  it("emotions VRM lacks borrow a preset, understated", () => {
    // `curious` borrowing `surprised` at full strength would read as shock.
    // **A near miss is better read quiet than loud.**
    const curious = applied(targetWeights(state({ emotion: "curious", intensity: 1 }), 0));
    expect(curious?.[0]).toBe("surprised");
    expect(curious?.[1]).toBeLessThan(0.5);
  });

  it("caps how far any one blend shape is pushed", () => {
    // VRM accepts 1.0, but a fully applied preset distorts most faces.
    // **A rendering detail — Core keeps sending 0.0-1.0.**
    for (const emotion of EMOTIONS) {
      const weights = targetWeights(state({ emotion, intensity: 1 }), 0);
      for (const preset of VRM_PRESETS) {
        expect(weights[preset]).toBeLessThanOrEqual(0.8);
      }
    }
  });

  it("clamps an out-of-range intensity instead of trusting it", () => {
    expect(applied(targetWeights(state({ emotion: "happy", intensity: 9 }), 0))?.[1]).toBe(0.8);
    expect(applied(targetWeights(state({ emotion: "happy", intensity: -1 }), 0))).toBeNull();
  });

  it("no expression means neutral", () => {
    expect(targetWeights(null, 0)).toEqual(EXPRESSION_NEUTRAL);
  });
});

describe("duration", () => {
  it("holds indefinitely when duration is null", () => {
    expect(applied(targetWeights(state({ durationMs: null }), 60_000))?.[0]).toBe("happy");
  });

  it("reverts to neutral once the duration has passed", () => {
    // **Leaving the last face on forever** would keep Lumi mid-thought long after the thought.
    const expression = state({ durationMs: 500 });
    expect(applied(targetWeights(expression, 499))?.[0]).toBe("happy");
    expect(targetWeights(expression, 500)).toEqual(EXPRESSION_NEUTRAL);
  });
});

describe("blending", () => {
  it("arrives exactly at the target, never merely approaching it", () => {
    // **`blend_ms` would describe nothing** if the blend only ever approached the target.
    const target = targetWeights(state({ emotion: "happy", intensity: 0.5 }), 0);
    let weights = EXPRESSION_NEUTRAL;
    for (let elapsed = 0; elapsed < 200; elapsed += 16) {
      weights = advanceExpression(weights, target, 16, 200);
    }
    expect(weights).toEqual(target);
  });

  it("moves at the rate blend_ms states", () => {
    // A full 0 → 1 sweep takes blend_ms, so half of one takes half the time.
    const target = targetWeights(state({ emotion: "happy", intensity: 0.5 }), 0);
    const half = advanceExpression(EXPRESSION_NEUTRAL, target, 100, 200);
    expect(half.happy).toBeCloseTo(0.5, 5);
  });

  it("blends back to neutral by the same rule", () => {
    // Reverting is not a special case. **The face never snaps back.**
    const from = targetWeights(state({ emotion: "angry", intensity: 0.8 }), 0);
    const back = advanceExpression(from, EXPRESSION_NEUTRAL, 16, 200);
    expect(back.angry).toBeGreaterThan(0);
    expect(back.angry).toBeLessThan(from.angry);
  });

  it("applies instantly when blend_ms is zero", () => {
    const target = targetWeights(state({ emotion: "sad", intensity: 0.5 }), 0);
    expect(advanceExpression(EXPRESSION_NEUTRAL, target, 16, 0)).toEqual(target);
  });

  it("moves partway in one frame, never all at once", () => {
    const target = targetWeights(state({ emotion: "happy", intensity: 0.8 }), 0);
    const one = advanceExpression(EXPRESSION_NEUTRAL, target, 16, 200);
    expect(one.happy).toBeGreaterThan(0);
    expect(one.happy).toBeLessThan(target.happy);
  });
});

describe("reading the payload", () => {
  it("reads what Core sent", () => {
    const parsed = parseExpression({
      emotion: "think",
      intensity: 0.5,
      blend_ms: 300,
      duration_ms: 1000,
    });
    expect(parsed).toEqual({ emotion: "think", intensity: 0.5, blendMs: 300, durationMs: 1000 });
  });

  it("treats a missing duration as hold", () => {
    expect(parseExpression({ emotion: "happy" })?.durationMs).toBeNull();
    expect(parseExpression({ emotion: "happy", duration_ms: null })?.durationMs).toBeNull();
  });

  it("refuses an unknown emotion rather than rounding it to neutral", () => {
    // **A face quietly going blank looks like a working expression.** Core/Stage drift
    // would show up as "emotions just don't come through" with nothing to point at.
    expect(parseExpression({ emotion: "excited" })).toBeNull();
    expect(parseExpression({})).toBeNull();
    expect(parseExpression({ emotion: 42 })).toBeNull();
  });

  it("falls back a malformed number to the default", () => {
    const parsed = parseExpression({ emotion: "happy", intensity: "strong", blend_ms: null });
    expect(parsed?.intensity).toBe(0.7);
    expect(parsed?.blendMs).toBe(200);
  });
});
