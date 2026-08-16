/**
 * リップシンク — **純粋関数**。
 *
 * 設計 → docs/interfaces/renderer.md「VisemeFrame（リップシンク）」
 *
 * Core は「いつどの口の形か」を**1回だけ**送ってくる。時刻を進めるのは Stage の仕事
 * （60Hz で送ると、Stage が詰まったときに口が固まる）。
 *
 * **アタック/リリースの非対称スムージングと無音判定は必須。**
 * 無いと口がガクガクする（.claude/rules/stage-ts.md）。
 */

export type Viseme = "A" | "I" | "U" | "E" | "O";

export const VISEMES: readonly Viseme[] = ["A", "I", "U", "E", "O"];

export interface VisemeSpan {
  /** `null` は口を閉じる（撥音・促音・無音）。 */
  viseme: Viseme | null;
  startMs: number;
  durationMs: number;
}

export interface VisemeTimeline {
  spans: VisemeSpan[];
  totalMs: number;
}

/** 各ビセームの強さ（0.0-1.0）。 */
export type MouthWeights = Readonly<Record<Viseme, number>>;

export const MOUTH_CLOSED: MouthWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };

/**
 * 口を開くのは速く、閉じるのは遅く。
 *
 * 同じ速さにすると、子音の切れ目ごとに口が閉じきってしまい、**震えて見える**。
 */
const ATTACK_TAU_S = 0.035;
const RELEASE_TAU_S = 0.09;

/**
 * `ended` が来なくても、この時間を過ぎたら口を閉じる。
 *
 * **Core が落ちても口が開きっぱなしにならない**（fail-closed）。
 * 再生の終わりと通知の到着には数十 ms のずれがあるので、少しだけ猶予を持たせる。
 */
export const TAIL_MS = 250;

/** その時刻に出すべき口の形。**タイムラインの外なら閉じる。** */
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

/** 指数的に目標へ近づける。`tau` が小さいほど速い。 */
function approach(current: number, target: number, deltaSeconds: number, tau: number): number {
  const rate = 1 - Math.exp(-deltaSeconds / tau);
  return current + (target - current) * rate;
}

/**
 * 1フレーム分だけ口を目標へ動かす。
 *
 * 目標が `null`（無音）のときは全部 0 に向かう = 口が閉じる。
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
    // 端で永遠に微小値が残らないようにする（0 に落ちないと「閉じた」判定ができない）。
    next[viseme] = value < 0.002 ? 0 : Math.min(value, 1);
  }
  return next;
}

/** 口が閉じているか。**無音判定**。 */
export function isMouthClosed(weights: MouthWeights): boolean {
  return VISEMES.every((viseme) => weights[viseme] === 0);
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Core の payload を読む。**壊れていたら `null`**（何も再生しないより悪いことをしない）。
 *
 * Core は信頼できる相手だが、**バージョン違いで形が変わることはある**。
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
