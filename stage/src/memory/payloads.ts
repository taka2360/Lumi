/**
 * Parsing what Core sends the memory window.
 *
 * **Fail-closed in the direction that matters here.** A row whose fields cannot be read
 * is dropped rather than rendered with blanks: a memory listed with an empty sentence and
 * a working [forget] button invites deleting something nobody could read.
 *
 * The primitives come from `core/payloads/read`, which is where the `stage.*` readers get
 * them too. **`asFiniteNumber` is deliberately not the same function as `asNumber`**: the
 * numbers here are counted with (totals, paging), so a `NaN` that reached the arithmetic
 * would produce an empty page rather than a visibly wrong one.
 */

import { asFiniteNumber, asString, isRecord } from "../core/payloads/read";

export interface MemoryItem {
  id: string;
  type: string;
  subject: string;
  content: string;
  assertion_mode: string;
  trust_level: string;
  confidence: number;
  effective_salience: number;
  valid_from: string;
  archived: boolean;
  superseded_by: string | null;
}

export interface MemoryPage {
  items: MemoryItem[];
  total: number;
  /**
   * Utterances no reflection pass has read yet.
   *
   * **This is what separates "Lumi remembers nothing about you" from "Lumi has not had a
   * quiet moment yet."** Both show an empty list, and only one of them means something
   * is wrong.
   */
  pendingTurns: number;
}

/** One row of privacy.md §2, and how many of it "erase everything" would remove. */
export interface EraseTarget {
  target: string;
  count: number;
}

export function toMemoryItem(value: unknown): MemoryItem | null {
  if (!isRecord(value)) {
    return null;
  }
  const id = asString(value.id);
  const subject = asString(value.subject);
  const content = asString(value.content);
  if (!id || !subject || !content) {
    return null;
  }
  return {
    id,
    type: asString(value.type) ?? "semantic",
    subject,
    content,
    assertion_mode: asString(value.assertion_mode) ?? "inferred",
    // **Unreadable trust reads as tainted**, never as trusted: the badge exists to warn,
    // and a missing field must not be the thing that removes the warning.
    trust_level: asString(value.trust_level) ?? "tainted",
    confidence: asFiniteNumber(value.confidence, 0),
    effective_salience: asFiniteNumber(value.effective_salience, 0),
    valid_from: asString(value.valid_from) ?? "",
    archived: value.archived === true,
    superseded_by: asString(value.superseded_by),
  };
}

export function toMemoryPage(payload: Record<string, unknown>): MemoryPage {
  const raw = Array.isArray(payload.items) ? payload.items : [];
  const items = raw.map(toMemoryItem).filter((item): item is MemoryItem => item !== null);
  // **The total is Core's**, so "showing 3 of 40" stays true even when a row was dropped
  // as unreadable — the gap is visible instead of being quietly closed.
  return {
    items,
    total: asFiniteNumber(payload.total, items.length),
    pendingTurns: asFiniteNumber(payload.pending_turns, 0),
  };
}

export function toEraseTargets(payload: Record<string, unknown>): EraseTarget[] {
  const raw = Array.isArray(payload.targets) ? payload.targets : [];
  return raw.flatMap((entry) => {
    if (!isRecord(entry)) {
      return [];
    }
    const target = asString(entry.target);
    return target ? [{ target, count: asFiniteNumber(entry.count, 0) }] : [];
  });
}
