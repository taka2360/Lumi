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
 * **Its own window since 2g** (ADR-042). It used to be drawn inside the character window
 * because a second `stage` connection would have taken the character's; it now connects as
 * `panel`, which may hold several connections and is never the addressee of a command.
 *
 * **Displays only.** Every value here was broadcast by Core; nothing is derived on this
 * side — including whether a span is over budget, which is Core's `unaccounted_ms` to say.
 */

import type { InspectorActivity, InspectorLatency } from "../core/store";
import { useStageStore } from "../core/store";
import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";

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
  const locale = useLocale();
  return (
    <table className="inspect__lat">
      <tbody>
        {orderedSpans(latency.spans).map(([name, value]) => (
          <tr key={name}>
            <th>{name}</th>
            <td>{value}</td>
          </tr>
        ))}
        {latency.speculation.overlap_ms > 0 && (
          <tr className="inspect__lat-hidden">
            {/* **How much of STT ran inside the VAD wait** (ADR-039). What is left of
                `stt_ms` after this is what the turn actually paid for it. */}
            <th>stt_overlap_ms</th>
            <td>-{latency.speculation.overlap_ms}</td>
          </tr>
        )}
        <tr className="inspect__lat-sum">
          <th>critical_path_ms</th>
          <td>{latency.critical_path_ms}</td>
        </tr>
        <tr className="inspect__lat-sum">
          <th>total_ms</th>
          <td>{latency.total_ms}</td>
        </tr>
        <tr className="inspect__lat-sum">
          {/* **The reserve's warning light.** Growth here means work nobody is measuring. */}
          <th>unaccounted_ms</th>
          <td>{latency.unaccounted_ms}</td>
        </tr>
        {latency.speculation.discarded > 0 && (
          <tr className="inspect__lat-note">
            {/* **Not a failure.** It means the user carried on talking, and the result of
                work already started stopped describing what they said. */}
            <th>{translate(locale, "inspector.discarded")}</th>
            <td>
              {latency.speculation.discarded} / {latency.speculation.discarded_ms}ms
            </td>
          </tr>
        )}
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

export function Inspector() {
  const locale = useLocale();
  const inspector = useStageStore((state) => state.inspector);

  if (!inspector) {
    // **Nothing has happened yet.** Core sends a snapshot on the first Activity
    // transition, so an empty window here means the conversation has not started.
    return <p className="inspect__empty">{translate(locale, "inspector.waiting")}</p>;
  }

  return (
    <div className="inspect">
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
  );
}
