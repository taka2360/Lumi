/**
 * Parsing what Core sends the memory window.
 *
 * **Fail-closed in the direction that matters here.** A row whose fields cannot be read
 * is dropped rather than rendered with blanks: a memory listed with an empty sentence and
 * a working [forget] button invites deleting something nobody could read.
 */

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
}

/** One row of privacy.md §2, and how many of it "erase everything" would remove. */
export interface EraseTarget {
  target: string;
  count: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
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
    confidence: asNumber(value.confidence, 0),
    effective_salience: asNumber(value.effective_salience, 0),
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
  return { items, total: asNumber(payload.total, items.length) };
}

export function toEraseTargets(payload: Record<string, unknown>): EraseTarget[] {
  const raw = Array.isArray(payload.targets) ? payload.targets : [];
  return raw.flatMap((entry) => {
    if (!isRecord(entry)) {
      return [];
    }
    const target = asString(entry.target);
    return target ? [{ target, count: asNumber(entry.count, 0) }] : [];
  });
}
