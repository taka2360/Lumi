/**
 * Inspector — **what is happening right now, and what it cost.**
 *
 * Design → docs/architecture/ui.md §5
 *
 * > Being able to trace "why did it say that just now" is a design requirement.
 * > Without it, Phase 6 cannot be tuned.
 *
 * Phase 1 shows the minimum: the Activity tree and the latency breakdown.
 *
 * **It lives inside the `stage` window**, not a window of its own. `WsServer` keeps at
 * most one connection per role, so a second window connecting as `stage` would take the
 * character's connection away from it (docs/architecture/ui.md).
 *
 * **Displays only.** Every value here was broadcast by Core; nothing is derived on this
 * side — including whether a span is over budget, which is Core's `unaccounted_ms` to say.
 */

import { useState } from "react";

import type { InspectorActivity, InspectorLatency } from "../core/store";
import { useStageStore } from "../core/store";
import { browserLocale, translate } from "../i18n";

/** Spans, in the order the turn actually passes through them (docs/architecture/audio.md §7). */
const SPAN_ORDER = [
  "vad_ms",
  "stt_ms",
  "retrieve_ms",
  "assemble_ms",
  "llm_first_token_ms",
  "llm_first_segment_ms",
  "tts_first_audio_ms",
  "playback_ms",
];

function orderedSpans(spans: Record<string, number>): [string, number][] {
  const known = SPAN_ORDER.filter((name) => name in spans).map(
    (name) => [name, spans[name]] as [string, number],
  );
  // **A span Core added but this list does not know about is still shown**, at the end.
  // Silently dropping it would hide exactly the new measurement someone just added.
  const extra = Object.entries(spans).filter(([name]) => !SPAN_ORDER.includes(name));
  return [...known, ...extra];
}

function ActivityRow({ activity }: { activity: InspectorActivity }) {
  return (
    <li className={activity.foreground ? "inspect__act inspect__act--fg" : "inspect__act"}>
      <span className="inspect__kind">{activity.kind}</span>
      <span className="inspect__state">{activity.state}</span>
      <span className="inspect__intent">{activity.intent}</span>
      {activity.cancellables.length > 0 && (
        <ul className="inspect__children">
          {activity.cancellables.map((child) => (
            <li key={child.label}>
              {/* **The child's own state, not the parent's.** A parent already cancelling
                  while a child has not stopped is the divergence this view exists for. */}
              <span className="inspect__state">{child.finished ? "stopped" : "running"}</span>
              <span>{child.label}</span>
              <span className="inspect__contract">{child.contract}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Latency({ latency }: { latency: InspectorLatency }) {
  const locale = browserLocale();
  return (
    <table className="inspect__lat">
      <tbody>
        {orderedSpans(latency.spans).map(([name, value]) => (
          <tr key={name}>
            <th>{name}</th>
            <td>{value}</td>
          </tr>
        ))}
        <tr className="inspect__lat-sum">
          <th>total_ms</th>
          <td>{latency.total_ms}</td>
        </tr>
        <tr className="inspect__lat-sum">
          {/* **The reserve's warning light.** Growth here means work nobody is measuring. */}
          <th>unaccounted_ms</th>
          <td>{latency.unaccounted_ms}</td>
        </tr>
        {!latency.completed && (
          <tr className="inspect__lat-cut">
            {/* Barge-in is the normal case, and **how far the turn got is the measurement.** */}
            <th colSpan={2}>{translate(locale, "inspector.interrupted")}</th>
          </tr>
        )}
      </tbody>
    </table>
  );
}

export function Inspector({ onOpenChange }: { onOpenChange?: (open: boolean) => void } = {}) {
  const locale = browserLocale();
  const inspector = useStageStore((state) => state.inspector);
  const [open, setOpen] = useState(false);

  if (!inspector) {
    // **Nothing has happened yet.** Never a button that opens an empty panel
    return null;
  }

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    onOpenChange?.(next);
  };

  return (
    <div className="inspect">
      <button type="button" className="inspect__toggle" onClick={handleToggle}>
        {open ? "▾" : "▸"} Inspector
      </button>
      {open && (
        <div className="inspect__body">
          <ul className="inspect__acts">
            {inspector.activities.map((activity) => (
              <ActivityRow key={activity.id} activity={activity} />
            ))}
          </ul>
          {inspector.latency ? (
            <Latency latency={inspector.latency} />
          ) : (
            <p className="inspect__empty">{translate(locale, "inspector.empty")}</p>
          )}
        </div>
      )}
    </div>
  );
}
