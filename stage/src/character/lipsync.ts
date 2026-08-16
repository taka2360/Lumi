/**
 * Lip sync — **pure functions**.
 *
 * Design → docs/interfaces/renderer.md "VisemeFrame (lip sync)"
 *
 * Core sends "when to use which mouth shape" **exactly once**. Advancing time is
 * the Stage's job (sending at 60Hz would freeze the mouth whenever the Stage stalls).
 *
 * **Asymmetric attack/release smoothing and silence detection are mandatory.**
 * Without them the mouth judders (.claude/rules/stage-ts.md).
 */

export type Viseme = "A" | "I" | "U" | "E" | "O";

export const VISEMES: readonly Viseme[] = ["A", "I", "U", "E", "O"];

export interface VisemeSpan {
  /** `null` closes the mouth (moraic nasal, geminate, or silence). */
  viseme: Viseme | null;
  startMs: number;
  durationMs: number;
}

export interface VisemeTimeline {
  spans: VisemeSpan[];
  totalMs: number;
}

/** The strength of each viseme (0.0-1.0). */
export type MouthWeights = Readonly<Record<Viseme, number>>;

export const MOUTH_CLOSED: MouthWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };

/**
 * Opens the mouth fast, closes it slowly.
 *
 * At the same speed, the mouth would fully close at every consonant boundary,
 * **making it look like it's trembling**.
 */
const ATTACK_TAU_S = 0.035;
const RELEASE_TAU_S = 0.09;

/**
 * Closes the mouth once this much time has passed, even without `ended` arriving.
 *
 * **The mouth never stays open forever even if Core crashes** (fail-closed).
 * There's a gap of tens of ms between playback ending and the notification
 * arriving, so a small grace period is given.
 */
export const TAIL_MS = 250;

/** The mouth shape to show at that time. **Closed if outside the timeline.** */
export function visemeAt(timeline: VisemeTimeline, elapsedMs: number): Viseme | null {
  if (elapsedMs < 0 || elapsedMs > timeline.totalMs + TAIL_MS) {
    return null;
  }
  for (const span of timeline.spans) {
    if (elapsedMs < span.startMs) {
      return null;
    }
    if (elapsedMs < span.startMs + span.durationMs) {
      return span.viseme;
    }
  }
  return null;
}

/** Exponentially approaches the target. Smaller `tau` means faster. */
function approach(current: number, target: number, deltaSeconds: number, tau: number): number {
  const rate = 1 - Math.exp(-deltaSeconds / tau);
  return current + (target - current) * rate;
}

/**
 * Moves the mouth toward the target by one frame's worth.
 *
 * When the target is `null` (silence), everything heads to 0 = the mouth closes.
 */
export function advanceMouth(
  current: MouthWeights,
  target: Viseme | null,
  deltaSeconds: number,
): MouthWeights {
  const next: Record<Viseme, number> = { ...MOUTH_CLOSED };
  for (const viseme of VISEMES) {
    const goal = viseme === target ? 1 : 0;
    const tau = goal > current[viseme] ? ATTACK_TAU_S : RELEASE_TAU_S;
    const value = approach(current[viseme], goal, deltaSeconds, tau);
    // Prevents an infinitesimal value from lingering forever at the tail end (without dropping to 0, "closed" can't be detected).
    next[viseme] = value < 0.002 ? 0 : Math.min(value, 1);
  }
  return next;
}

/** Whether the mouth is closed. **Silence detection.** */
export function isMouthClosed(weights: MouthWeights): boolean {
  return VISEMES.every((viseme) => weights[viseme] === 0);
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Reads Core's payload. **`null` if malformed** (never does anything worse than playing nothing).
 *
 * Core is a trusted peer, but **its shape can still change across versions**.
 */
export function parseTimeline(payload: Record<string, unknown>): VisemeTimeline | null {
  const totalMs = asNumber(payload.total_ms);
  const rawSpans = payload.spans;
  if (totalMs === null || totalMs < 0 || !Array.isArray(rawSpans)) {
    return null;
  }

  const spans: VisemeSpan[] = [];
  for (const raw of rawSpans) {
    if (typeof raw !== "object" || raw === null) {
      return null;
    }
    const span = raw as Record<string, unknown>;
    const startMs = asNumber(span.start_ms);
    const durationMs = asNumber(span.duration_ms);
    if (startMs === null || durationMs === null || startMs < 0 || durationMs < 0) {
      return null;
    }
    const viseme = VISEMES.find((candidate) => candidate === span.viseme) ?? null;
    spans.push({ viseme, startMs, durationMs });
  }
  return { spans, totalMs };
}
