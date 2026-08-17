/**
 * Idle motion — **pure functions**.
 *
 * Time is received as an argument (never calls `performance.now()` inside a test
 * → .claude/rules/tests.md "time is passed as an argument").
 *
 * Phase 0 only has procedural breathing and swaying. Playing motion clips is Phase 1 onward.
 */

export interface IdlePose {
  /** Vertical sway (meters). */
  offsetY: number;
  /** Left-right tilt (radians). */
  tiltZ: number;
  /** Scale from breathing. 1.0 is the baseline. */
  breathScale: number;
}

/** 呼吸周期（秒） */
const BREATH_PERIOD_S = 4.0;
/** 体の揺れ周期（秒） */
const SWAY_PERIOD_S = 6.7;

export function computeIdlePose(elapsedSeconds: number): IdlePose {
  const breath = Math.sin((elapsedSeconds / BREATH_PERIOD_S) * Math.PI * 2);
  const sway = Math.sin((elapsedSeconds / SWAY_PERIOD_S) * Math.PI * 2);
  return {
    offsetY: breath * 0.012,
    tiltZ: sway * 0.015,
    breathScale: 1 + breath * 0.004,
  };
}
