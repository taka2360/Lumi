/**
 * Reading `stage.inspector.state` (delivered over the `panel` connection since ADR-042).
 *
 * **Everything is read defensively even though Core is a trusted peer.** The Inspector is
 * the view people reach for *while something is already wrong*; it throwing on a
 * half-written payload would take away the tool exactly when it is needed.
 */

import type { InspectorActivity, InspectorLatency, InspectorSnapshot } from "../store";
import { asNumber, asString, isRecord } from "./read";

/**
 * Reads a `stage.inspector.state` payload.
 *
 * **Everything is read defensively even though Core is a trusted peer.** The Inspector is
 * the view people reach for *while something is already wrong*; it throwing on a
 * half-written payload would take away the tool exactly when it is needed.
 */
export function toInspectorSnapshot(payload: Record<string, unknown>): InspectorSnapshot {
  const activities = Array.isArray(payload.activities) ? payload.activities : [];
  return {
    activities: activities.filter(isRecord).map(toInspectorActivity),
    latency: isRecord(payload.latency) ? toInspectorLatency(payload.latency) : null,
  };
}

function toInspectorActivity(raw: Record<string, unknown>): InspectorActivity {
  const cancellables = Array.isArray(raw.cancellables) ? raw.cancellables : [];
  return {
    id: asString(raw.id) ?? "",
    kind: asString(raw.kind) ?? "?",
    actor: asString(raw.actor) ?? "?",
    intent: asString(raw.intent) ?? "",
    state: asString(raw.state) ?? "?",
    priority: asNumber(raw.priority) ?? 0,
    foreground: raw.foreground === true,
    cancellables: cancellables.filter(isRecord).map((item) => ({
      label: asString(item.label) ?? "",
      contract: asString(item.contract) ?? "?",
      finished: item.finished === true,
    })),
  };
}

function toInspectorLatency(raw: Record<string, unknown>): InspectorLatency {
  // Core flattens the spans into the payload, so **anything numeric that is not one of
  // the summary fields is a span.** Adding a span on the Core side then needs no change here.
  //
  // **A summary field left out of this set becomes a fake span** in the Inspector, listed
  // among the stages of a turn as though the turn passed through it. That is why the
  // speculation keys are here: they are facts about `stt_ms`, not stages of their own.
  const summary = new Set([
    "correlation_id",
    "measured_sum_ms",
    "critical_path_ms",
    "total_ms",
    "unaccounted_ms",
    "completed",
    "stt_speculative",
    "stt_overlap_ms",
    "stt_wait_ms",
    "stt_discarded_ms",
    "stt_discarded",
  ]);
  const spans: Record<string, number> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!summary.has(key) && typeof value === "number") {
      spans[key] = value;
    }
  }
  const measured_sum_ms = asNumber(raw.measured_sum_ms) ?? 0;
  return {
    correlation_id: asString(raw.correlation_id) ?? "",
    spans,
    measured_sum_ms,
    // **Falls back to the sum**, which is what it equals when nothing overlapped.
    critical_path_ms: asNumber(raw.critical_path_ms) ?? measured_sum_ms,
    total_ms: asNumber(raw.total_ms) ?? 0,
    unaccounted_ms: asNumber(raw.unaccounted_ms) ?? 0,
    completed: raw.completed === true,
    speculation: {
      speculative: raw.stt_speculative === true,
      overlap_ms: asNumber(raw.stt_overlap_ms) ?? 0,
      wait_ms: asNumber(raw.stt_wait_ms) ?? 0,
      discarded_ms: asNumber(raw.stt_discarded_ms) ?? 0,
      discarded: asNumber(raw.stt_discarded) ?? 0,
    },
  };
}
