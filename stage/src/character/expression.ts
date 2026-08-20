/**
 * Expressions — **pure functions**.
 *
 * Design → docs/interfaces/renderer.md / ADR-009
 *
 * Core sends an **intent** (`happy` at 0.7), never parameters. Which blend shape that
 * becomes, and what to do when there isn't one, is **this file's business and no one
 * else's** — that is the whole reason Live2D (Phase 9) can be added without touching Core.
 *
 * As with the mouth, Core states the target **once** and the Stage advances time itself.
 */

/**
 * Values that go on the wire. **`docs/contracts/wire.json` is authoritative** (→ ADR-022).
 * Order matches Core's `Emotion` declaration order.
 */
export const EMOTIONS = [
  "neutral",
  "happy",
  "sad",
  "angry",
  "surprised",
  "think",
  "curious",
  "awkward",
  "sleepy",
] as const;

export type Emotion = (typeof EMOTIONS)[number];

export interface ExpressionIntent {
  emotion: Emotion;
  /** 0.0-1.0. **How this maps to an actual value range is a rendering detail.** */
  intensity: number;
  blendMs: number;
  /** `null` holds it. With a value, it reverts on its own. */
  durationMs: number | null;
}

/**
 * The VRM 1.0 standard expression names. **Only these are safe to assume exist**; anything
 * else varies per model.
 */
export const VRM_PRESETS = ["neutral", "happy", "sad", "angry", "surprised", "relaxed"] as const;

export type VrmPreset = (typeof VRM_PRESETS)[number];

export type ExpressionWeights = Readonly<Record<VrmPreset, number>>;

export const EXPRESSION_NEUTRAL: ExpressionWeights = {
  neutral: 0,
  happy: 0,
  sad: 0,
  angry: 0,
  surprised: 0,
  relaxed: 0,
};

/**
 * Intent → the preset that carries it, and how much of the intensity survives.
 *
 * **Four of the nine emotions have no VRM preset.** Core is right to have them —
 * `think` and `awkward` are things Lumi genuinely does — and **the Renderer falling back
 * on its own is the contract** (docs/interfaces/renderer.md: Core never branches on
 * `capabilities()`).
 *
 * The scale is what keeps a fallback honest: `curious` borrowing `surprised` at full
 * strength would read as shock. **A near miss is better read quiet than loud.**
 */
const MAPPING: Readonly<Record<Emotion, { preset: VrmPreset; scale: number }>> = {
  neutral: { preset: "neutral", scale: 1 },
  happy: { preset: "happy", scale: 1 },
  sad: { preset: "sad", scale: 1 },
  angry: { preset: "angry", scale: 1 },
  surprised: { preset: "surprised", scale: 1 },
  // Borrowed, deliberately understated
  think: { preset: "relaxed", scale: 0.5 },
  curious: { preset: "surprised", scale: 0.4 },
  awkward: { preset: "sad", scale: 0.45 },
  sleepy: { preset: "relaxed", scale: 0.8 },
};

/**
 * The upper bound on any one blend shape.
 *
 * VRM accepts 1.0, but a fully applied preset distorts most faces. **Capping is a
 * rendering detail** and is exactly what Core is not supposed to know
 * (docs/interfaces/renderer.md).
 */
const MAX_WEIGHT = 0.8;

export interface ExpressionState {
  intent: ExpressionIntent;
  /** When it arrived (`performance.now()`). */
  startedAtMs: number;
}

/**
 * The weights being aimed at right now.
 *
 * **Reverts to neutral once `durationMs` has elapsed** — the whole point of the field.
 * Holding the last expression forever would leave Lumi stuck mid-thought long after
 * the thought ended.
 */
export function targetWeights(state: ExpressionState | null, nowMs: number): ExpressionWeights {
  if (!state) {
    return EXPRESSION_NEUTRAL;
  }
  const { intent, startedAtMs } = state;
  if (intent.durationMs !== null && nowMs - startedAtMs >= intent.durationMs) {
    return EXPRESSION_NEUTRAL;
  }
  const { preset, scale } = MAPPING[intent.emotion];
  const clamped = Math.min(Math.max(intent.intensity, 0), 1);
  return { ...EXPRESSION_NEUTRAL, [preset]: Math.min(clamped * scale, MAX_WEIGHT) };
}

/**
 * Moves the weights toward the target by one frame's worth. **At a constant rate.**
 *
 * Not the mouth's asymmetric attack/release: a face changes once per utterance, not once
 * per mora, so there is nothing to smooth out. And **not an exponential approach** —
 * moving a fraction of what remains never actually arrives, which would make `blendMs`
 * a number that describes nothing.
 *
 * **`blendMs` is the time for a full 0 → 1 sweep.** A change of 0.5 therefore lands in
 * half that. Stating it as a rate is what keeps this stateless: reverting after
 * `durationMs` blends by the same rule, from wherever the face happens to be.
 */
export function advanceExpression(
  current: ExpressionWeights,
  target: ExpressionWeights,
  deltaMs: number,
  blendMs: number,
): ExpressionWeights {
  // A non-positive blend means "now". Guards a divide-by-zero as well.
  const step = blendMs <= 0 ? 1 : deltaMs / blendMs;
  const next: Record<VrmPreset, number> = { ...EXPRESSION_NEUTRAL };
  for (const preset of VRM_PRESETS) {
    const remaining = target[preset] - current[preset];
    next[preset] =
      Math.abs(remaining) <= step ? target[preset] : current[preset] + Math.sign(remaining) * step;
  }
  return next;
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/**
 * Reads a `stage.character.expression` payload. **`null` for an unknown emotion.**
 *
 * Never rounded to `neutral`: a face that quietly goes blank looks like a working
 * expression, so a Core/Stage drift would show as "the emotions just don't come
 * through" with nothing to point at.
 */
export function parseExpression(payload: Record<string, unknown>): ExpressionIntent | null {
  const emotion = EMOTIONS.find((candidate) => candidate === payload.emotion);
  if (!emotion) {
    return null;
  }
  const duration = payload.duration_ms;
  return {
    emotion,
    intensity: asNumber(payload.intensity, 0.7),
    blendMs: asNumber(payload.blend_ms, 200),
    durationMs: typeof duration === "number" && Number.isFinite(duration) ? duration : null,
  };
}
