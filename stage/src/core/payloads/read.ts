/**
 * Reading unknown values off the wire. **The primitives every payload reader is built on.**
 *
 * These answer one question — "is this field the shape I expected, and what do I use if it
 * is not?" — and they answer it the same way everywhere: **fall back rather than throw**.
 * Core is a trusted peer, but a parse error in the Stage would take a view away exactly
 * when something is already wrong and the user has gone looking for it.
 *
 * The cost is that drift is silent. `wire.test.ts` is what makes it loud again.
 */

/**
 * Picks `value` out of `candidates`, or falls back. **`fallback` is required.**
 *
 * Types vanish at runtime, so checking a received value against a union needs an
 * actual array. Making the fallback an argument rather than an optional means
 * **every caller has to state what an unknown value degrades to** — and every one of
 * them degrades to the safe side, never to the permissive one.
 */
export function oneOf<T extends string>(candidates: readonly T[], value: unknown, fallback: T): T {
  return candidates.find((candidate) => candidate === value) ?? fallback;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/**
 * A number, or `null` when the field is missing or is not one.
 *
 * **Deliberately does not reject `NaN` or infinities**, and deliberately separate from
 * `asFiniteNumber`. The `stage.*` readers pass what they get straight through to a
 * display, where a wrong number is visible; the memory readers use it for paging
 * arithmetic, where one would silently produce an empty page.
 */
export function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/**
 * A number that can be counted with, or `fallback`.
 *
 * **The distinct name is the point.** This and `asNumber` were the same name in two
 * files with two different contracts — one returning `null`, one taking a fallback and
 * screening non-finite values. Merging them would have quietly changed what a malformed
 * field degrades to on one side or the other.
 */
export function asFiniteNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** A nested object off a payload, or `{}` — so a reader can go on reading fields from it. */
export function part(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  return isRecord(payload[key]) ? (payload[key] as Record<string, unknown>) : {};
}
